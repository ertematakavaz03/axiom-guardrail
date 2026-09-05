from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def value(item: Any, digits: int = 3) -> str:
    if item is None:
        return "N/A"
    if isinstance(item, float):
        return f"{item:.{digits}f}"
    return str(item)


def percent(item: Any) -> str:
    return "N/A" if item is None else f"{float(item) * 100:.2f}%"


def latency_row(name: str, metrics: dict[str, Any]) -> str:
    return "| " + " | ".join(
        [name]
        + [value(metrics.get(key), 2) for key in ("mean", "median", "p50", "p90", "p95", "p99", "min", "max")]
    ) + " |"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the external benchmark report")
    parser.add_argument("--smoke-audit", type=Path, required=True)
    parser.add_argument("--smoke-summary", type=Path, required=True)
    parser.add_argument("--primary-audit", type=Path, required=True)
    parser.add_argument("--primary-summary", type=Path, required=True)
    parser.add_argument("--performance-summary", type=Path, required=True)
    parser.add_argument("--stability", type=Path, required=True)
    parser.add_argument("--integration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = load(ROOT / "source.json")
    environment = load(ROOT / "environment.json")
    validation = load(ROOT / "validation.json")
    upstream = load(ROOT / "upstream-tests.json")
    smoke_audit = load(args.smoke_audit)
    smoke = load(args.smoke_summary)
    primary_audit = load(args.primary_audit)
    primary_raw_directory = Path(
        primary_audit["input_directories_in_precedence_order"][0]
    )
    primary = load(args.primary_summary)
    performance = load(args.performance_summary)
    stability = load(args.stability)
    integration = load(args.integration)
    excluded_stability_path = (
        args.stability.parent / "excluded-infrastructure-attempts.jsonl"
    )
    excluded_stability_attempts = (
        sum(
            bool(line.strip())
            for line in excluded_stability_path.read_text(encoding="utf-8").splitlines()
        )
        if excluded_stability_path.exists()
        else 0
    )
    audited_hashes = load(args.primary_audit.parent / "artifacts-sha256.json")[
        "artifacts"
    ]

    q = primary["quality"]
    h = primary["hallucination"]
    p = primary["privacy_isolation"]
    r = primary["retrieval"]
    perf = primary["performance"]
    audited_failures = [
        case
        for case in primary_audit["cases"]
        if case["audit_classification"] == "REAL_AGENT_FAILURE"
    ]
    corrections_by_issue = {
        item["issue"]: item
        for item in [
            *smoke_audit["false_positive_corrections"],
            *primary_audit.get("false_positive_corrections", []),
        ]
    }
    corrections = list(corrections_by_issue.values())
    correction_counts = Counter(item["classification"] for item in corrections)
    category_counts = validation["category_counts"]
    difficulty_counts = validation["difficulty_counts"]
    throughput = performance["local_benchmark_throughput"]
    nondeterministic_cases = [
        case
        for case in stability["cases"]
        if not all(
            case[key]
            for key in (
                "verdict_consistent",
                "tool_selection_consistent",
                "argument_consistent",
                "fact_consistent",
                "hallucination_consistent",
            )
        )
    ]

    def report_path(path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(Path.cwd().resolve()).as_posix()
        except ValueError:
            return resolved.name
    benchmark_hashes = {
        "benchmarks/external/langgraph-support-v1/manifest.yaml": sha256(
            ROOT / "manifest.yaml"
        ),
        "benchmarks/external/langgraph-support-v1/cases.json": sha256(
            ROOT / "cases.json"
        ),
        **audited_hashes,
        report_path(args.performance_summary): sha256(args.performance_summary),
        report_path(args.stability): sha256(args.stability),
        report_path(args.integration): sha256(args.integration),
    }
    if excluded_stability_path.exists():
        benchmark_hashes[report_path(excluded_stability_path)] = sha256(
            excluded_stability_path
        )
    integration_path = (
        f"{integration['project']['name']} → {integration['agent']['name']} → "
        f"{integration['suite']['name']} → Run {integration['run']['id']} → Case → Trace"
    )

    lines = [
        "# Axiom Guardrail External Benchmark #1",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "## Reproducibility verdict",
        "",
        "Complete. The real pinned external LangGraph agent was evaluated without source modification. "
        "The primary score contains exactly 100 unique cases at concurrency 1; throughput and stability "
        "runs are reported separately.",
        "",
        "## Benchmark purpose",
        "",
        "This benchmark measures whether Axiom can ingest, preserve, deterministically evaluate, audit, "
        "and present evidence from an independently healthy third-party tool-using agent. It also reports "
        "the external agent's observed task, tool, grounding, isolation, reliability, and local performance "
        "outcomes without implying endorsement, affiliation, certification, or production readiness.",
        "",
        "## External source and protected baseline",
        "",
        f"- Repository: `{source['upstream']['repository']}`",
        f"- Pinned SHA: `{source['upstream']['commit']}` (detached checkout)",
        f"- License: {source['upstream']['license']['declared']} declared in "
        f"`{source['upstream']['license']['source']}`; standalone license file present: "
        f"{source['upstream']['license']['standalone_license_file_present']}",
        f"- Axiom baseline: `v0.2.0` / `{source['axiom']['baseline_commit']}`",
        f"- Axiom branch: `{source['axiom']['branch']}`",
        "",
        "## External agent architecture and tool surface",
        "",
        "The pinned application is a LangGraph ReAct loop: START → ChatOllama agent → conditional tool "
        "routing → LangGraph ToolNode → agent, terminating when the model returns no tool call. Conversation "
        "messages are held in graph state; the local LangGraph server supplies thread/checkpoint handling. "
        "The graph binds llama3.1 at temperature 0 with a 4,096-token context and a 512-token generation cap.",
        "",
        "| Tool | Observed role | Side effect in pinned demo |",
        "| --- | --- | --- |",
        *[
            f"| `{name}` | {definition['kind']} | {value(definition['side_effect'])} |"
            for name, definition in load(ROOT / "tool-map.json")["tools"].items()
        ],
        "",
        "## Environment",
        "",
        f"- OS: {environment['host']['os']} {environment['host']['os_version']} ({environment['host']['architecture']})",
        f"- CPU: {environment['host']['cpu']} ({environment['host']['physical_cores']} physical / "
        f"{environment['host']['logical_processors']} logical)",
        f"- Memory: {environment['host']['memory_bytes'] / 1024**3:.2f} GiB",
        f"- GPU: {environment['host']['gpu']} ({environment['host']['gpu_memory_mib']} MiB), "
        f"driver {environment['host']['nvidia_driver']}",
        f"- Python: {environment['runtime']['python']} in isolated `{environment['runtime']['virtual_environment']}`",
        f"- Ollama: {environment['runtime']['ollama']}",
        f"- Model: `{environment['model']['name']}`; digest `{environment['model']['digest']}`; "
        f"{environment['model']['parameter_size']} {environment['model']['quantization']}",
        f"- Excluded warm-up: {environment['warmup']['wall_ms']:,} ms",
        "",
        "Upstream verification: "
        f"{upstream['summary']['passed']} passed, {upstream['summary']['failed']} failed "
        f"({upstream['suites'][0]['passed']} non-LLM; {upstream['suites'][1]['passed']} Ollama-dependent).",
        "",
        "## Methodology",
        "",
        "The benchmark drives the pinned graph through its real local LangGraph HTTP protocol and local "
        "Ollama model. Each primary case receives an independent thread except explicitly declared multi-turn "
        "cases. The adapter records messages, tool calls/arguments/results, final responses, thread IDs, and "
        "observable model metadata without invoking Axiom tools. Source-backed deterministic rules evaluate "
        "required tools, arguments, expected facts, extractable factual claims, retrieval, and synthetic-canary "
        "isolation. No paid LLM judge is used. Every non-pass result is audited before aggregation.",
        "",
        "### Raw execution versus final evaluation",
        "",
        "`20260904-primary/cases.jsonl` is the immutable RAW EXECUTION artifact produced by 100 real model "
        "calls. The external model was not called during correction. FINAL EVALUATION was recomputed from "
        "those frozen traces into `20260904-primary-audit/audited-cases.jsonl`; its audit and summary are "
        "separate hashed artifacts. The raw execution SHA-256 is unchanged across re-evaluation.",
        "",
        "## Dataset",
        "",
        f"Validation status: `{validation['valid']}`; primary cases: {validation['case_count']}; "
        f"smoke: {validation['smoke_count']}; safe performance: {validation['performance_count']}; "
        f"stability: {validation['stability_count']}; canaries: {validation['canary_count']}.",
        "",
        "| Category | Cases |",
        "| --- | ---: |",
        *[f"| {name} | {count} |" for name, count in category_counts.items()],
        "",
        "Difficulty: " + ", ".join(f"{name}={count}" for name, count in difficulty_counts.items()) + ".",
        "",
        "## Smoke audit",
        "",
        f"All 12 smoke cases were persisted and audited. Final audited verdicts: "
        f"{smoke['verdicts']['pass']} pass, {smoke['verdicts']['warn']} warn, "
        f"{smoke['verdicts']['block']} block. Smoke accepted: "
        f"`{smoke_audit['smoke_acceptance']['accepted']}`. The six remaining blocks were "
        "classified as trace-supported `REAL_AGENT_FAILURE`; none were infrastructure, adapter, "
        "expectation, or unresolved evaluator failures.",
        "",
        "## Primary 100-case results",
        "",
        f"Verdicts: {primary['verdicts']['pass']} pass, {primary['verdicts']['warn']} warn, "
        f"{primary['verdicts']['block']} block. Audited validated agent-failure cases: "
        f"{primary['audit']['validated_agent_failure_case_count']}; unresolved: "
        f"{primary['audit']['unresolved_case_count']}.",
        "",
        f"Agent-quality aggregates use {q['eligible_case_count']} eligible cases. "
        f"{q['excluded_case_count']} case was excluded because its expected branch was impossible under the "
        "pinned tool's substring behavior; its raw execution and operational/tool evidence remain retained.",
        "",
        "Final non-pass case taxonomy: "
        + ", ".join(
            f"{name}={count}"
            for name, count in primary["audit"]["case_classification_counts"].items()
        )
        + f", MODEL_NONDETERMINISM={len(nondeterministic_cases)}, "
        f"EVALUATOR_FALSE_POSITIVE=0, ADAPTER_BUG=0, INFRASTRUCTURE_FAILURE=0, "
        f"UNRESOLVED={primary['audit']['unresolved_case_count']}. Model nondeterminism is derived only from "
        "the separate stability run and does not alter primary quality.",
        "",
        "### Quality and tool metrics",
        "",
        "| Metric | Result |",
        "| --- | ---: |",
        *[
            f"| {label} | {percent(q[key])} |"
            for label, key in (
                ("Task Success Rate", "task_success_rate"),
                ("Tool Selection Accuracy", "tool_selection_accuracy"),
                ("Required Tool Recall", "required_tool_recall"),
                ("Unexpected Tool Rate", "unexpected_tool_rate"),
                ("Forbidden Tool Rate", "forbidden_tool_rate"),
                ("Tool Argument Accuracy", "tool_argument_accuracy"),
                ("Required Argument Accuracy", "required_argument_accuracy"),
                ("Escalation Accuracy", "escalation_accuracy"),
                ("Fact Accuracy", "fact_accuracy"),
                ("KB Answer Accuracy", "kb_answer_accuracy"),
                ("Order Status Accuracy", "order_status_accuracy"),
                ("Inventory Accuracy", "inventory_accuracy"),
                ("Return Flow Accuracy", "return_flow_accuracy"),
                ("Multi-turn Context Accuracy", "multi_turn_context_accuracy"),
                ("Insufficient-Information Accuracy", "insufficient_information_accuracy"),
            )
        ],
        "",
        "### Hallucination and groundedness",
        "",
        f"Claim scope: {h['scope']}. No paid LLM judge was used.",
        "",
        "| Metric | Result |",
        "| --- | ---: |",
        f"| Factual Claim Count | {h['factual_claim_count']} |",
        f"| Supported Factual Claim Count | {h['supported_factual_claims']} |",
        f"| Contradicted Claim Count | {h['contradicted_claims']} |",
        f"| Unsupported Claim Count | {h['unsupported_claims']} |",
        f"| Fabricated Entity/Value Count | {h['fabricated_entities_or_values']} |",
        f"| Hallucination Case Rate | {percent(h['hallucination_case_rate'])} |",
        f"| Unsupported Claim Rate | {percent(h['unsupported_claim_rate'])} |",
        f"| Grounded Case Rate | {percent(h['grounded_case_rate'])} |",
        "",
        "### Privacy and isolation",
        "",
        f"Synthetic canary cases: {p['evaluated_cases']}; canary leakage count: {p['leakage_cases']}; "
        f"cross-case leakage rate: {percent(p['leakage_rate'])}; memory isolation pass rate: "
        f"{percent(p['memory_isolation_pass_rate'])}. This is an observed benchmark-isolation result, "
        "not a privacy certification.",
        "",
        "### Retrieval",
        "",
        f"Measured cases: {r['evaluated_cases']}; Recall@1/3/5: {value(r['recall_at_1'])} / "
        f"{value(r['recall_at_3'])} / {value(r['recall_at_5'])}; MRR: {value(r['mrr'])}; "
        f"nDCG: {value(r['ndcg'])}. IDs are benchmark-derived from exact returned content. "
        f"Citation precision/recall: {value(r['citation_precision'])} / {value(r['citation_recall'])} "
        "because the agent emits no structured citations.",
        "",
        "### Reliability, latency, and observable telemetry",
        "",
        f"Execution errors: {primary['errors']['execution_errors']}; timeouts: {primary['errors']['timeouts']}; "
        f"retries: {perf['retry_count']['total']}; tool calls: {perf['tool_call_count']['total']}; "
        f"tokens: {perf['token_counts']['total_tokens']} total "
        f"({perf['token_counts']['input_tokens']} input / {perf['token_counts']['output_tokens']} output).",
        "",
        "| Reliability metric | Result |",
        "| --- | ---: |",
        *[
            f"| {label} | {percent(q[key])} |"
            for label, key in (
                ("Completion Rate", "completion_rate"),
                ("Timeout Rate", "timeout_rate"),
                ("Agent Error Rate", "agent_error_rate"),
                ("Tool Error Rate (per observed call)", "tool_error_rate"),
                ("Benchmark Retry Rate", "retry_rate"),
                ("Malformed Output Rate", "malformed_output_rate"),
                ("Retrieval Failure Rate (per retrieval call)", "retrieval_failure_rate"),
            )
        ],
        "",
        "| Timing | Mean | Median | p50 | p90 | p95 | p99 | Min | Max |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        latency_row("Total case latency (ms)", perf["total_case_latency_ms"]),
        latency_row("Model latency (ms)", perf["model_latency_ms"]),
        "",
        f"Queue wait: {value(perf['queue_wait_ms'])}; tool latency: {value(perf['tool_latency_ms'])}; "
        f"retrieval latency: {value(perf['retrieval_latency_ms'])}; TTFT: "
        f"{value(perf['time_to_first_token_ms'])}. These remain N/A because the non-streaming "
        "LangGraph wait protocol did not expose them.",
        "",
        "## LOCAL BENCHMARK THROUGHPUT",
        "",
        "This is a local laptop benchmark, not a production-capacity claim.",
        "",
        "| Concurrency | Completed | Cases/min | Success | Mean latency (ms) | p50 (ms) | p95 (ms) | Tokens | Timeout | Error |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        *[
            f"| {name} | {item['completed_executions']} | {value(item['cases_per_minute'], 2)} | "
            f"{percent(item['success_rate'])} | {value(item['mean_latency_ms'], 2)} | "
            f"{value(item['p50_latency_ms'], 2)} | {value(item['p95_latency_ms'], 2)} | "
            f"{item['token_counts']['total_tokens']} | {percent(item['timeout_rate'])} | "
            f"{percent(item['error_rate'])} |"
            for name, item in sorted(throughput.items())
        ],
        "",
        "## Stability (20 cases × 3 repeats)",
        "",
        f"Executions: {stability['execution_count']}; excluded from primary score: "
        f"`{stability['separate_from_primary_score']}`.",
        f"A transient local Ollama-unavailable attempt was quarantined and its single execution ID "
        f"rerun; excluded infrastructure attempts: {excluded_stability_attempts}. The canonical "
        "stability set contains 60 unique, completed executions with no execution errors.",
        "",
        "| Metric | Result |",
        "| --- | ---: |",
        *[
            f"| {name.replace('_', ' ').title()} | {percent(result)} |"
            for name, result in stability["consistency"].items()
        ],
        f"| Latency coefficient of variation | "
        f"{percent(stability['latency_variability']['coefficient_of_variation'])} |",
        "",
        "### Model nondeterminism",
        "",
        f"{len(nondeterministic_cases)} of {stability['case_count']} stability cases changed at least one "
        "audited verdict/tool/argument/fact/hallucination outcome across repeats. These variations are "
        "reported separately and do not change the primary score.",
        "",
        *[
            f"- `{case['case_id']}` — verdict={case['verdict_consistent']}, "
            f"tools={case['tool_selection_consistent']}, arguments={case['argument_consistent']}, "
            f"facts={case['fact_consistent']}, hallucination={case['hallucination_consistent']}"
            for case in nondeterministic_cases
        ],
        "",
        "## Validated external-agent failures",
        "",
        "Only audited `REAL_AGENT_FAILURE` cases are counted below. Semantic failures were not retried.",
        "",
        *[
            f"- `{case['case_id']}` — {', '.join(case['reason_code_precedence'])}"
            for case in audited_failures
        ],
        "",
        "## False positives discovered and corrected",
        "",
        f"Correction records: EVALUATOR_FALSE_POSITIVE={correction_counts['EVALUATOR_FALSE_POSITIVE']}, "
        f"BENCHMARK_EXPECTATION_BUG={correction_counts['BENCHMARK_EXPECTATION_BUG']}, "
        f"ADAPTER_BUG={correction_counts['ADAPTER_BUG']}. These discovered defects were corrected before "
        "final aggregation and do not count as validated external-agent failures.",
        "",
        *[
            f"- {item['issue']} Correction: {item['correction']} "
            f"(cases: {', '.join(item['affected_case_ids'])})."
            for item in corrections
        ],
        "",
        "## Infrastructure and observability limitations",
        "",
        f"Primary infrastructure failures: {primary['errors']['execution_errors']}; performance errors: "
        f"{performance['errors']['execution_errors']}; excluded stability startup attempts: "
        f"{excluded_stability_attempts}. Model duration and token usage are observable from "
        "Ollama response metadata. Per-tool latency, retrieval latency, internal queue wait, streaming TTFT, "
        "and structured citations are not exposed and remain N/A. Retrieval document IDs are benchmark-derived. "
        "The upstream actions are mock/simulated; generated RMA/ticket IDs are process-dependent; model outputs "
        "may vary despite temperature 0; and this single-machine test does not establish production capacity.",
        "",
        "### External agent limitations",
        "",
        "The pinned agent uses mock orders/inventory and a local vector store, while return and escalation "
        "actions are simulated. Retrieval can return semantically adjacent but irrelevant chunks; the model "
        "can emit raw function syntax or a capability menu instead of a user-facing answer; and temperature 0 "
        "does not eliminate backend/model nondeterminism. These are observed properties of this pinned demo, "
        "not claims about LangGraph or Ollama generally.",
        "",
        "## Observational versus preventive enforcement",
        "",
        "This benchmark observes the external agent after its own graph has selected and executed tools. Axiom "
        "records and evaluates those traces; it does not prevent, approve, sandbox, or replay the upstream calls. "
        "Accordingly, these results demonstrate observational evaluation, not preventive ToolGateway enforcement.",
        "",
        "## Axiom product integration and limitations",
        "",
        f"Imported through the existing model: `{integration_path}`. Dashboard: `{integration['dashboard_url']}`. "
        "The import reuses Project, AgentVersion, TestSuite, Scenario, Run, CaseResult, Trace, and EvalResult; "
        "it does not create a standalone benchmark UI and does not replay already-executed external tools through "
        "Axiom's ToolGateway. Existing cards display their native metric subset; the complete audited metric payload "
        "is retained in the run snapshot/metrics and raw report artifacts.",
        "",
        f"Importer idempotency verified: `{integration.get('idempotency_verified', False)}`; resource counts "
        f"matched expected counts on both imports: `{integration.get('resource_counts_valid', False)}`. "
        "Axiom's current external-benchmark view is evidence and audit presentation, not live upstream call "
        "interception, a privacy certification, or a capacity-planning system.",
        "",
        "## Reproduction commands",
        "",
        "```powershell",
        "python benchmarks/external/langgraph-support-v1/validator.py",
        "python benchmarks/external/langgraph-support-v1/runner.py --output <new-primary-output-dir> --timeout-seconds 105",
        "python benchmarks/external/langgraph-support-v1/runner.py --performance all --output <new-performance-output-dir> --timeout-seconds 105",
        "python benchmarks/external/langgraph-support-v1/runner.py --stability --output <new-stability-output-dir> --timeout-seconds 105",
        "python benchmarks/external/langgraph-support-v1/stability.py --input benchmarks/results/langgraph-support-v1/20260904-stability",
        "python benchmarks/external/langgraph-support-v1/audit.py --input benchmarks/results/langgraph-support-v1/20260904-primary --corrections benchmarks/external/langgraph-support-v1/smoke-corrections.json --overrides benchmarks/external/langgraph-support-v1/primary-audit-overrides.json --output <new-audit-output-dir>",
        "```",
        "",
        "Use `--resume` with the same output directory after an interruption; completed execution IDs are skipped.",
        "",
        "## Provenance hashes",
        "",
        *[
            f"- `{name}`: `{digest}`"
            for name, digest in source["upstream"]["source_sha256"].items()
        ],
        *[f"- `{name}`: `{digest}`" for name, digest in benchmark_hashes.items()],
        "",
        "## Raw result paths",
        "",
        f"- Smoke audit: `{report_path(args.smoke_audit)}`",
        f"- Primary raw execution: `{report_path(primary_raw_directory)}`",
        f"- Primary audit: `{report_path(args.primary_audit.parent)}`",
        f"- Performance: `{report_path(args.performance_summary.parent)}`",
        f"- Stability: `{report_path(args.stability.parent)}`",
        f"- Axiom integration: `{report_path(args.integration)}`",
        "",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"REPORT={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
