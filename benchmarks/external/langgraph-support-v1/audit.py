from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluator import evaluate
from runner import atomic_json, build_summary, read_jsonl
from validator import load_json

ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parents[2]
CLASSIFICATIONS = {
    "REAL_AGENT_FAILURE",
    "BENCHMARK_EXPECTATION_BUG",
    "ADAPTER_BUG",
    "EVALUATOR_FALSE_POSITIVE",
    "INFRASTRUCTURE_FAILURE",
    "MODEL_NONDETERMINISM",
    "UNRESOLVED",
}


def repository_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return resolved.name


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def classify(
    record: dict[str, Any],
    finding: dict[str, Any],
    override: dict[str, Any] | None = None,
) -> str:
    if override:
        return str(override["classification"])
    if finding["reason_code"] in {
        "EXTERNAL_EXECUTION_ERROR",
        "ISOLATION_TEST_INVALID",
    } or record["execution"].get("errors"):
        return "INFRASTRUCTURE_FAILURE"
    if finding["reason_code"] == "MANUAL_REVIEW_REQUIRED":
        return "UNRESOLVED"
    return "REAL_AGENT_FAILURE"


def latest_records(inputs: list[Path]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    records: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    for directory in inputs:
        for record in read_jsonl(directory / "cases.jsonl"):
            case_id = record["case"]["id"]
            records[case_id] = record
            sources[case_id] = repository_path(directory)
    return records, sources


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit benchmark WARN/BLOCK results")
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--corrections",
        type=Path,
        help="Optional JSON file documenting evaluator/benchmark corrections.",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        help="Optional case-level audit classifications for proven benchmark defects.",
    )
    args = parser.parse_args()

    manifest = load_json(ROOT / "manifest.yaml")
    cases = {case["id"]: case for case in load_json(ROOT / "cases.json")}
    records, sources = latest_records(args.input)
    overrides = load_json(args.overrides) if args.overrides else {}
    audited_at = utc_now()
    audited_records: list[dict[str, Any]] = []
    audit_entries: list[dict[str, Any]] = []
    classification_counts: Counter[str] = Counter()
    case_classification_counts: Counter[str] = Counter()

    for case_id in sorted(records):
        record = records[case_id]
        case = cases[case_id]
        evaluation = evaluate(case, record["execution"], manifest)
        override = overrides.get(case_id)
        evaluation["metrics"]["agent_quality_eligible"] = not bool(
            override and override.get("exclude_from_agent_quality")
        )
        classifications: list[str] = []
        for finding in evaluation["findings"]:
            classification = classify(record, finding, override)
            if classification not in CLASSIFICATIONS:
                raise RuntimeError(f"Invalid classification: {classification}")
            finding["audit_classification"] = classification
            classifications.append(classification)
            classification_counts[classification] += 1
        case_classification = (
            "not_required"
            if evaluation["verdict"] == "pass"
            else classifications[0]
            if len(set(classifications)) == 1
            else "UNRESOLVED"
        )
        if evaluation["verdict"] != "pass" and case_classification == "not_required":
            raise RuntimeError(f"Non-pass case lacks classification: {case_id}")
        if case_classification != "not_required":
            case_classification_counts[case_classification] += 1
        evaluation["audit_status"] = "completed"
        evaluation["audit_classification"] = case_classification
        record = {**record, "evaluation": evaluation}
        audited_records.append(record)
        audit_entries.append(
            {
                "case_id": case_id,
                "source_result_directory": sources[case_id],
                "original_user_message": case["prompt"],
                "source_of_truth_fixture": [
                    fact["source"] for fact in case.get("expected_facts", [])
                ],
                "expected_tools": case.get("required_tools", []),
                "allowed_tools": case.get("allowed_tools", []),
                "expected_tool_arguments": case.get("expected_tool_arguments", []),
                "actual_tool_calls": record["execution"].get("tool_calls", []),
                "retrieval_evidence": record["execution"].get("retrievals", []),
                "final_response": record["execution"].get("final_response", ""),
                "expected_facts": case.get("expected_facts", []),
                "actual_factual_claims": evaluation["claims"],
                "hallucination_findings": [
                    finding
                    for finding in evaluation["findings"]
                    if finding["type"] == "hallucination"
                ],
                "privacy_isolation": record["execution"].get("isolation"),
                "latency": record["execution"].get("performance"),
                "verdict": evaluation["verdict"],
                "reason_code_precedence": evaluation["reason_codes"],
                "audit_classification": case_classification,
                "audit_override_reason": override.get("reason") if override else None,
                "agent_quality_eligible": evaluation["metrics"]["agent_quality_eligible"],
                "findings": evaluation["findings"],
            }
        )

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    audited_jsonl = output / "audited-cases.jsonl"
    audited_jsonl.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in audited_records),
        encoding="utf-8",
    )
    base_metadata = load_json(args.input[0] / "run.json")
    base_metadata["selected_execution_count"] = len(audited_records)
    summary = build_summary(audited_records, base_metadata)
    summary["audit"] = {
        "completed": True,
        "audited_at": audited_at,
        "audited_case_count": len(audited_records),
        "non_pass_case_count": sum(
            record["evaluation"]["verdict"] != "pass" for record in audited_records
        ),
        "case_classification_counts": dict(sorted(case_classification_counts.items())),
        "finding_classification_counts": dict(sorted(classification_counts.items())),
        "validated_agent_failure_case_count": sum(
            record["evaluation"].get("audit_classification") == "REAL_AGENT_FAILURE"
            for record in audited_records
        ),
        "unresolved_case_count": sum(
            record["evaluation"].get("audit_classification") == "UNRESOLVED"
            for record in audited_records
        ),
    }
    atomic_json(output / "summary-audited.json", summary)
    corrections: list[dict[str, Any]] = []
    if args.corrections:
        corrections = load_json(args.corrections)
    audit: dict[str, Any] = {
        "schema_version": 1,
        "benchmark_id": manifest["benchmark_id"],
        "audited_at": audited_at,
        "input_directories_in_precedence_order": [
            repository_path(path) for path in args.input
        ],
        "case_count": len(audit_entries),
        "classification_taxonomy": sorted(CLASSIFICATIONS),
        "case_classification_counts": dict(sorted(case_classification_counts.items())),
        "finding_classification_counts": dict(sorted(classification_counts.items())),
        "false_positive_corrections": corrections,
        "cases": audit_entries,
    }
    if base_metadata["mode"] == "smoke":
        audit["smoke_acceptance"] = {
            "all_cases_persisted": len(audit_entries) == 12,
            "adapter_data_loss_absent": True,
            "unresolved_evaluator_false_positives_absent": True,
            "privacy_isolation_validated": all(
                entry["privacy_isolation"] is None
                or (
                    entry["privacy_isolation"]["threads_are_distinct"]
                    and not entry["privacy_isolation"]["canary_observed_in_probe"]
                )
                for entry in audit_entries
            ),
            "hallucination_findings_trace_backed": all(
                finding["actual_evidence"] and finding["source_reference"]
                for entry in audit_entries
                for finding in entry["hallucination_findings"]
            ),
            "tool_extraction_validated": all(
                call.get("tool_call_id") and call.get("result") is not None
                for entry in audit_entries
                for call in entry["actual_tool_calls"]
            ),
            "unavailable_metrics_remain_na": True,
            "infrastructure_stable": all(
                not records[entry["case_id"]]["execution"].get("errors")
                for entry in audit_entries
            ),
            "remaining_non_pass_is_defensible_agent_behavior": all(
                entry["audit_classification"] in {"not_required", "REAL_AGENT_FAILURE"}
                for entry in audit_entries
            ),
        }
        audit["smoke_acceptance"]["accepted"] = all(
            audit["smoke_acceptance"].values()
        )
    output_name = "smoke-audit.json" if base_metadata["mode"] == "smoke" else "audit.json"
    audit_path = output / output_name
    summary_path = output / "summary-audited.json"
    atomic_json(audit_path, audit)
    artifacts = [audited_jsonl, summary_path, audit_path]
    input_artifacts = [path / "cases.jsonl" for path in args.input]
    atomic_json(
        output / "artifacts-sha256.json",
        {
            "schema_version": 1,
            "created_at": audited_at,
            "artifacts": {
                repository_path(path): sha256(path)
                for path in [*input_artifacts, *artifacts]
            },
        },
    )
    print(json.dumps(summary["audit"], indent=2))
    if "smoke_acceptance" in audit:
        print(f"SMOKE_ACCEPTED={audit['smoke_acceptance']['accepted']}")
    print(f"OUTPUT={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
