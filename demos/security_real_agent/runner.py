"""Execution harness for security-real-agent-v1.

Transport is injected, so everything except the HTTP call is a pure function and unit
testable. ``main`` wires the pinned upstream adapter from
``benchmarks/external/langgraph-support-v1/adapter.py`` by file path: that directory is
not an importable package (its name contains a hyphen) and is deliberately left
unmodified by this phase.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from demos.security_real_agent import BENCHMARK_ID
from demos.security_real_agent.classify import EXPLICIT_REFUSAL_RULES, classify
from demos.security_real_agent.corpus import config_for, corpus
from demos.security_real_agent.enforcement import SHADOW_POLICY_VERSION, shadow_decisions
from demos.security_real_agent.models import (
    CASE_SCHEMA_VERSION,
    EXTRACTION_EVIDENCE_POLICY,
    RealAgentCaseResult,
    RealAgentScenario,
)
from demos.security_real_agent.report import REPORT_VERSION, aggregate
from services.security.models import digest
from services.security.trace_detector import (
    DETECTOR_VERSION,
    AgentTrace,
    TraceMessage,
    TraceToolCall,
    detect,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_DIR = REPO_ROOT / "benchmarks" / "external" / "langgraph-support-v1"
#: sys.modules key for the dynamically loaded upstream adapter. Distinct from any
#: importable package name so it cannot shadow a real module.
UPSTREAM_ADAPTER_MODULE = "axiom_upstream_langgraph_support_v1_adapter"
RESULTS_ROOT = REPO_ROOT / "benchmarks" / "results" / BENCHMARK_ID

#: Executor signature: (case_id, turns) -> raw execution record from the upstream adapter.
Transport = Callable[[str, list[str]], dict[str, Any]]

PILOT_IDS = [
    "rsa-001",  # direct injection + side effect
    "rsa-005",  # role impersonation + foreign identifier
    "rsa-017",  # argument manipulation
    "rsa-021",  # relayed untrusted content
    "rsa-025",  # system prompt extraction (unscorable without markers)
    "rsa-029",  # confidentiality constraint
    "rsb-001",  # benign knowledge-base read
    "rsb-007",  # benign owned-order lookup
    "rsb-011",  # benign authorised side effect
    "rsb-023",  # benign security vocabulary
    "rsb-025",  # benign hostile tone
    "rsb-032",  # benign ambiguous request
]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_trace(scenario: RealAgentScenario, execution: dict[str, Any]) -> AgentTrace:
    """Normalise one upstream execution record into detector input. Pure."""
    messages = [
        TraceMessage(
            id=str(item.get("id", f"message-{index}")),
            role=str(item.get("role", "unknown")),
            content=str(item.get("content", "")),
        )
        for index, item in enumerate(execution.get("messages", []))
    ]
    calls = [
        TraceToolCall(
            tool_call_id=str(item.get("tool_call_id", f"call-{index}")),
            name=str(item.get("name", "")),
            arguments=item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
            result=item.get("result"),
            status=item.get("status") if item.get("status") is None else str(item.get("status")),
        )
        for index, item in enumerate(execution.get("tool_calls", []))
    ]
    retrieved = [
        json.dumps(item, ensure_ascii=False, sort_keys=True)
        for item in execution.get("retrievals", [])
    ]
    errors = [str(item) for item in execution.get("errors", [])]
    return AgentTrace(
        case_id=scenario.id,
        user_inputs=list(scenario.turns),
        retrieved_content=retrieved,
        messages=messages,
        tool_calls=calls,
        final_response=str(execution.get("final_response", "")),
        runtime_errors=errors,
        completed=not errors,
    )


def evaluate(
    scenario: RealAgentScenario,
    trace: AgentTrace,
    *,
    system_prompt_markers: list[str] | None = None,
) -> RealAgentCaseResult:
    """Detect, shadow-evaluate and classify one case. Pure."""
    markers = system_prompt_markers or []
    config = config_for(scenario, markers)
    detection = detect(trace, config)
    shadow = shadow_decisions(
        trace,
        allow_side_effects=scenario.gold.authorizes_side_effects,
        authorized_tool_names=scenario.gold.authorized_tool_names,
    )
    return classify(
        scenario,
        trace,
        detection,
        shadow,
        config,
        system_prompt_markers_available=bool(markers),
    )


def select(
    scenarios: Sequence[RealAgentScenario], identifiers: Sequence[str]
) -> list[RealAgentScenario]:
    wanted = set(identifiers)
    chosen = [scenario for scenario in scenarios if scenario.id in wanted]
    missing = wanted - {scenario.id for scenario in chosen}
    if missing:
        raise ValueError(f"unknown scenario ids: {sorted(missing)}")
    return chosen


def run(
    scenarios: Sequence[RealAgentScenario],
    transport: Transport,
    *,
    system_prompt_markers: list[str] | None = None,
) -> tuple[list[RealAgentCaseResult], dict[str, int]]:
    """Execute scenarios and classify each one. Runtime failures are recorded, not hidden."""
    results: list[RealAgentCaseResult] = []
    runtime = {"malformed_outputs": 0, "timeouts": 0, "retries": 0}
    for scenario in scenarios:
        try:
            execution = transport(scenario.id, list(scenario.turns))
        except Exception as exc:  # noqa: BLE001 - a failure is evidence, never a defence
            message = f"{type(exc).__name__}: {exc}"
            if "timeout" in message.lower():
                runtime["timeouts"] += 1
            execution = {
                "messages": [],
                "tool_calls": [],
                "final_response": "",
                "errors": [message],
            }
        if not str(execution.get("final_response", "")).strip() and not execution.get("errors"):
            runtime["malformed_outputs"] += 1
        trace = build_trace(scenario, execution)
        results.append(evaluate(scenario, trace, system_prompt_markers=system_prompt_markers))
    return results, runtime


def upstream_pin() -> dict[str, Any]:
    """Read the pinned upstream identity without modifying the baseline benchmark."""
    source = json.loads((UPSTREAM_DIR / "source.json").read_text(encoding="utf-8"))
    manifest = json.loads((UPSTREAM_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    environment_path = UPSTREAM_DIR / "environment.json"
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    return {
        "upstream_repository": source["upstream"]["repository"],
        "upstream_commit": source["upstream"]["commit"],
        "upstream_source_sha256": source["upstream"]["source_sha256"],
        "graph_id": manifest["upstream"]["graph_id"],
        "declared_tools": sorted(manifest["tools"]),
        "model": manifest["model"],
        "baseline_recorded_runtime": environment["runtime"],
        "baseline_recorded_model_config": {
            key: environment["model"][key]
            for key in ("name", "digest", "parameter_size", "quantization", "format")
        },
        "phase35_supplemental_pin": json.loads(
            (Path(__file__).parent / "upstream_pin_supplement.json").read_text(encoding="utf-8")
        ),
        "pin_source_files": {
            "source.json": sha256_file(UPSTREAM_DIR / "source.json"),
            "manifest.yaml": sha256_file(UPSTREAM_DIR / "manifest.yaml"),
            "environment.json": sha256_file(environment_path),
            "upstream_pin_supplement.json": sha256_file(
                Path(__file__).parent / "upstream_pin_supplement.json"
            ),
        },
    }


def _command_output(command: list[str], timeout: float = 15.0) -> str | None:
    """Best-effort capture of a tool version. Never raises, never fatal to a run."""
    if shutil.which(command[0]) is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _version(command: list[str], pattern: str = r"(\d+\.\d+\.\d+)") -> str | None:
    output = _command_output(command)
    if output is None:
        return None
    match = re.search(pattern, output)
    return match.group(1) if match else output.splitlines()[0].strip()


def actual_runtime() -> dict[str, Any]:
    """Capture the runtime this process is actually executing in.

    Every value is measured at run time. Nothing here is a constant: a version that
    cannot be determined is reported as ``None`` with a status, following the project's
    "represent unavailable telemetry as null with status N/A, never estimate it" rule.
    """
    captured = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ollama": _version(["ollama", "--version"]),
        "docker_engine": _version(["docker", "version", "--format", "{{.Server.Version}}"]),
        "docker_compose": _version(["docker", "compose", "version", "--short"]),
    }
    unavailable = sorted(key for key, value in captured.items() if value is None)
    return {
        **captured,
        "capture_method": "measured_at_run_time",
        "unavailable": unavailable,
        "unavailable_status": "N/A_tool_not_present_or_not_queryable",
    }


#: A full sha256 digest as Ollama reports it, with or without the "sha256:" prefix.
_FULL_DIGEST = re.compile(r"\b(?:sha256[:-])?([0-9a-f]{64})\b")


def actual_model_digest(model: str) -> dict[str, Any]:
    """Observe the locally installed digest for ``model``, recording how it was obtained.

    ``ollama list`` prints only a 12-character short ID, so it can establish a prefix
    match and nothing more. ``ollama show --json`` exposes the full 64-character digest
    on the versions that support it. The observation records which one was actually seen
    so a report can never claim full digest identity from a 12-character prefix.
    """
    shown = _command_output(["ollama", "show", model, "--json"])
    if shown:
        match = _FULL_DIGEST.search(shown)
        if match:
            return {
                "value": match.group(1),
                "length": 64,
                "source": "ollama show --json",
            }
    listed = _command_output(["ollama", "list"])
    if listed:
        for line in listed.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] == model:
                return {
                    "value": fields[1],
                    "length": len(fields[1]),
                    "source": "ollama list (short id column)",
                }
    return {"value": None, "length": 0, "source": None}


def digest_comparison(expected: str, observed: dict[str, Any]) -> dict[str, Any]:
    """Classify an expected/observed digest pair without overstating the evidence."""
    value = observed.get("value")
    if not value:
        return {
            "expected": expected,
            "observed": None,
            "observed_source": None,
            "match_kind": "unverifiable",
            "proves_full_digest_identity": False,
            "status": "N/A_ollama_not_queryable",
        }
    normalised = str(value).lower().removeprefix("sha256:").removeprefix("sha256-")
    if len(normalised) == len(expected):
        match_kind = "full_digest_match" if normalised == expected else "mismatch"
    elif expected.startswith(normalised):
        match_kind = f"prefix_match_{len(normalised)}"
    else:
        match_kind = "mismatch"
    return {
        "expected": expected,
        "observed": normalised,
        "observed_source": observed.get("source"),
        "observed_length": len(normalised),
        "match_kind": match_kind,
        "proves_full_digest_identity": match_kind == "full_digest_match",
        "status": "measured_from_ollama",
    }


def runtime_drift(baseline: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """Compare the historical pinned runtime with the one actually used.

    Reports ``identical: false`` whenever any comparable key differs, so a report can
    never imply the environment was byte-identical to the pinned baseline when it was
    not. Keys present in only one side are listed rather than silently dropped.
    """
    comparable = sorted(set(baseline) & set(actual))
    differences = {
        key: {"baseline": baseline[key], "actual": actual[key]}
        for key in comparable
        if str(baseline[key]) != str(actual[key])
    }
    return {
        "compared_keys": comparable,
        "baseline_only_keys": sorted(set(baseline) - set(actual)),
        "actual_only_keys": sorted(key for key in set(actual) - set(baseline)),
        "differences": differences,
        "identical": not differences,
        "summary": [
            f"{key}: {value['baseline']} -> {value['actual']}"
            for key, value in sorted(differences.items())
        ],
        "note": (
            "Baseline is the runtime recorded when the pinned upstream benchmark was "
            "captured. It is historical metadata and is never overwritten. Actual is "
            "measured during this run."
        ),
    }


#: Every link of the marker resolution chain that must survive into the run artifact.
#: Losing any one of them breaks the audit: without both file names and both hashes a
#: reader cannot re-derive the markers, and without the assignment span they cannot find
#: the text inside the resolved module.
REQUIRED_MARKER_PROVENANCE_FIELDS = frozenset(
    {
        "entry_source",
        "entry_source_sha256",
        "model_visible_construct",
        "model_visible_line",
        "model_visible_binding",
        "import_statement",
        "import_line",
        "resolved_source",
        "resolved_source_sha256",
        "assignment_symbol",
        "assignment_lineno",
        "assignment_end_lineno",
        "assignment_construct",
    }
)


def load_markers(path: Path | None) -> tuple[list[str], dict[str, Any]]:
    """Load system-prompt markers plus the provenance of how they were derived.

    Accepts a record produced by :mod:`demos.security_real_agent.markers` (preferred,
    carries the pinned source hash) or a bare list. A bare list is accepted but recorded
    as unverified provenance so a report can never imply the markers were pinned.
    """
    if path is None:
        return [], {"status": "absent", "markers_available": False}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        markers = [str(item) for item in payload.get("markers", [])]
        # The resolution chain is copied through verbatim rather than flattened. The
        # pilot-2 run artifact reported source_file/source_sha256 as null because
        # marker-selection-3 records the chain under "provenance" — two files and two
        # hashes, which a single source_file field cannot represent. Reporting null for
        # the provenance of a pinned artifact is worse than reporting nothing, because it
        # reads as "no pinned source". See defect ledger D-013.
        chain = payload.get("provenance")
        provenance_chain = dict(chain) if isinstance(chain, dict) else {}
        record = {
            "status": "derived_from_pinned_source",
            "markers_available": bool(markers),
            "selection_rule": payload.get("selection_rule"),
            "derived_from_model_output": payload.get("derived_from_model_output", False),
            "marker_count": len(markers),
            "prompt_sha256": payload.get("prompt_sha256"),
            "scoring_limitation": payload.get("scoring_limitation"),
            "confound_corpora": payload.get("confound_corpora", []),
            "candidates_examined": payload.get("candidates_examined"),
            "candidates_rejected_count": len(payload.get("candidates_rejected", [])),
            "resolution_chain": provenance_chain,
        }
        missing = sorted(REQUIRED_MARKER_PROVENANCE_FIELDS - set(provenance_chain))
        if missing:
            # Fail loudly rather than emit a record that looks complete but is not.
            record["status"] = "incomplete_provenance"
            record["missing_provenance_fields"] = missing
        return markers, record
    markers = [str(item) for item in payload]
    return markers, {
        "status": "unverified_literal_list",
        "markers_available": bool(markers),
        "marker_count": len(markers),
        "note": "no pinned-source hash recorded; prefer demos.security_real_agent.markers",
    }


def provenance() -> dict[str, Any]:
    """Assemble upstream pin, baseline runtime, measured runtime and their drift."""
    pin = upstream_pin()
    baseline = dict(pin["baseline_recorded_runtime"])
    measured = actual_runtime()
    expected_digest = str(pin["baseline_recorded_model_config"]["digest"])
    return {
        "upstream": pin,
        "baseline_recorded_runtime": baseline,
        "actual_runtime": measured,
        "runtime_drift": runtime_drift(baseline, measured),
        "model_digest": digest_comparison(
            expected_digest, actual_model_digest(str(pin["model"]["name"]))
        ),
    }


def corpus_statistics(scenarios: Sequence[RealAgentScenario]) -> dict[str, Any]:
    attacks = [item for item in scenarios if item.is_attack]
    authored = [item for item in attacks if item.source == "authored"]
    variants = [item for item in attacks if item.source == "variant"]
    controls = [item for item in scenarios if not item.is_attack]
    return {
        "attack_cases": len(attacks),
        "authored_attack_cases": len(authored),
        "variant_attack_cases": len(variants),
        "unique_semantic_parents": len({item.semantic_parent_id or item.id for item in attacks}),
        "attack_families": sorted({item.family for item in authored}),
        "benign_controls": len(controls),
        "benign_families": sorted({item.family for item in controls}),
        "unique_prompts": len({item.model_visible_input for item in scenarios}),
        "corpus_digest": digest([item.model_dump(mode="json") for item in scenarios]),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the security-real-agent-v1 benchmark")
    parser.add_argument("--base-url", default="http://127.0.0.1:8123")
    parser.add_argument("--assistant-id", default="agent")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--output", default=str(RESULTS_ROOT / "pilot"))
    parser.add_argument("--profile", choices=["pilot", "full"], default="pilot")
    parser.add_argument("--system-prompt-markers", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the corpus and print statistics without calling the model",
    )
    return parser.parse_args(argv)


def load_upstream_adapter_module() -> ModuleType:
    """Import the pinned upstream adapter by file path.

    The ``sys.modules`` registration before ``exec_module`` is load-bearing, not
    housekeeping. ``benchmarks/external/langgraph-support-v1/adapter.py`` begins with
    ``from __future__ import annotations``, so its ``@dataclass`` field annotations are
    strings. On Python 3.12 ``dataclasses._is_type`` resolves them through
    ``sys.modules[cls.__module__].__dict__`` while the class is being created. Without
    the entry that lookup returns ``None`` and class creation fails with
    ``AttributeError: 'NoneType' object has no attribute '__dict__'`` before the module
    finishes importing. ``tests/unit/test_upstream_adapter_loader.py`` pins both the
    working path and the exact regression.

    The upstream file itself is never modified; only this loader is responsible for
    importing it correctly.
    """
    cached = sys.modules.get(UPSTREAM_ADAPTER_MODULE)
    if cached is not None:
        return cached
    adapter_path = UPSTREAM_DIR / "adapter.py"
    spec = importlib.util.spec_from_file_location(UPSTREAM_ADAPTER_MODULE, adapter_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load the pinned upstream adapter at {adapter_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # Never leave a half-executed module registered for the next caller to find.
        sys.modules.pop(spec.name, None)
        raise
    return module


def _load_adapter(base_url: str, assistant_id: str, timeout_seconds: float) -> Any:
    module = load_upstream_adapter_module()
    return module.LangGraphHttpAdapter(
        base_url, assistant_id=assistant_id, timeout_seconds=timeout_seconds
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    scenarios = corpus()
    selected = select(scenarios, PILOT_IDS) if args.profile == "pilot" else list(scenarios)
    statistics = corpus_statistics(scenarios)
    if args.dry_run:
        print(json.dumps({"corpus": statistics, "selected": len(selected)}, indent=2))
        return 0

    markers, marker_provenance = load_markers(
        Path(args.system_prompt_markers) if args.system_prompt_markers else None
    )

    adapter = _load_adapter(args.base_url, args.assistant_id, args.timeout_seconds)

    def transport(case_id: str, turns: list[str]) -> dict[str, Any]:
        result = adapter.execute(case_id, turns)
        return dict(result)

    started = utc_now()
    results, runtime = run(selected, transport, system_prompt_markers=markers)
    finished = utc_now()

    output = Path(args.output)
    cases_path = output / "cases.jsonl"
    atomic_write(
        cases_path,
        "\n".join(json.dumps(case.model_dump(mode="json"), sort_keys=True) for case in results)
        + "\n",
    )
    metadata = {
        "benchmark_id": BENCHMARK_ID,
        "profile": args.profile,
        "started_at": started,
        "finished_at": finished,
        "detector_version": DETECTOR_VERSION,
        "shadow_policy_version": SHADOW_POLICY_VERSION,
        "report_version": REPORT_VERSION,
        "case_schema_version": CASE_SCHEMA_VERSION,
        "extraction_evidence_policy": EXTRACTION_EVIDENCE_POLICY,
        "explicit_refusal_rules_registered": len(EXPLICIT_REFUSAL_RULES),
        "system_prompt_markers_available": bool(markers),
        "system_prompt_marker_provenance": marker_provenance,
        "corpus": statistics,
        "provenance": provenance(),
        "runtime": runtime,
        "enforcement_mode": "shadow_observational",
        "prevention_status": "N/A_no_host_owned_executor",
    }
    atomic_write(output / "run.json", json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    summary = aggregate(results, metadata)
    atomic_write(output / "summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    atomic_write(
        output / "artifacts-sha256.json",
        json.dumps(
            {
                name: sha256_file(output / name)
                for name in ("cases.jsonl", "run.json", "summary.json")
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    print(json.dumps(summary["counts"], indent=2))
    print(json.dumps(summary["agent_robustness"], indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
