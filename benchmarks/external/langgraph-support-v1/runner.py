from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adapter import ExternalAgentError, LangGraphHttpAdapter
from evaluator import evaluate
from validator import load_json, validate

ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parents[2]
RESULTS_ROOT = REPOSITORY_ROOT / "benchmarks" / "results" / "langgraph-support-v1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def atomic_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return records


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def latency_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {
            key: None
            for key in ("mean", "median", "p50", "p90", "p95", "p99", "min", "max")
        }
    return {
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "min": min(values),
        "max": max(values),
    }


def build_summary(records: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
    evaluations = [record["evaluation"] for record in records]
    quality_records = [
        record
        for record in records
        if record["evaluation"]["metrics"].get("agent_quality_eligible", True)
    ]
    quality_evaluations = [record["evaluation"] for record in quality_records]
    completed = len(records)
    verdicts = Counter(item["verdict"] for item in evaluations)
    latency_values = [
        float(record["execution"]["performance"]["total_case_latency_ms"])
        for record in records
        if isinstance(
            record.get("execution", {}).get("performance", {}).get("total_case_latency_ms"),
            (int, float),
        )
    ]
    model_latency_values = [
        float(record["execution"]["performance"]["model_latency_ms"])
        for record in records
        if isinstance(
            record.get("execution", {}).get("performance", {}).get("model_latency_ms"),
            (int, float),
        )
    ]
    claims = sum(item["metrics"]["factual_claim_count"] for item in quality_evaluations)
    unsupported = sum(
        item["metrics"]["unsupported_claims"]
        + item["metrics"]["fabricated_entities_or_values"]
        for item in quality_evaluations
    )
    measured_retrieval = [
        item["metrics"]["retrieval"]
        for item in quality_evaluations
        if item["metrics"]["retrieval"]["status"] != "N/A"
    ]

    def measured_usage(record: dict[str, Any]) -> dict[str, int] | None:
        usage = record["execution"].get("usage")
        if isinstance(usage, dict) and all(
            isinstance(usage.get(key), int)
            for key in ("input_tokens", "output_tokens", "total_tokens")
        ):
            return {key: int(usage[key]) for key in usage}
        prompt_tokens = 0
        completion_tokens = 0
        available = False
        for message in record["execution"].get("messages", []):
            model_metadata = message.get("model_metadata")
            if not isinstance(model_metadata, dict):
                continue
            prompt_count = model_metadata.get("prompt_eval_count")
            eval_count = model_metadata.get("eval_count")
            if isinstance(prompt_count, int):
                available = True
                prompt_tokens += prompt_count
            if isinstance(eval_count, int):
                available = True
                completion_tokens += eval_count
        if not available:
            return None
        return {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    measured_usages = [usage for record in records if (usage := measured_usage(record))]

    def weighted_ratio(
        numerator: str,
        denominator: str,
        *,
        empty: float | None = None,
        candidates: list[dict[str, Any]] | None = None,
    ) -> float | None:
        selected = candidates if candidates is not None else evaluations
        numerator_total = sum(
            int(item["metrics"].get(numerator, 0)) for item in selected
        )
        denominator_total = sum(
            int(item["metrics"].get(denominator, 0)) for item in selected
        )
        return numerator_total / denominator_total if denominator_total else empty

    def category_success(category: str) -> float | None:
        matching = [
            record["evaluation"]["metrics"]["task_success"]
            for record in quality_records
            if record["case"]["category"] == category
        ]
        return sum(bool(value) for value in matching) / len(matching) if matching else None

    def average(name: str) -> float | None:
        values = [
            float(item["metrics"][name])
            for item in evaluations
            if item["metrics"].get(name) is not None
        ]
        return statistics.fmean(values) if values else None

    def retrieval_average(name: str) -> float | None:
        values = [
            float(item[name]) for item in measured_retrieval if item.get(name) is not None
        ]
        return statistics.fmean(values) if values else None

    summary: dict[str, Any] = {
        "schema_version": 1,
        "benchmark_id": "langgraph-support-v1",
        "mode": metadata["mode"],
        "updated_at": utc_now(),
        "selected_execution_count": metadata["selected_execution_count"],
        "completed_execution_count": completed,
        "verdicts": {name: verdicts.get(name, 0) for name in ("pass", "warn", "block")},
        "quality": {
            "eligible_case_count": len(quality_records),
            "excluded_case_count": completed - len(quality_records),
            "task_success_rate": (
                sum(bool(item["metrics"]["task_success"]) for item in quality_evaluations)
                / len(quality_evaluations)
                if quality_evaluations
                else None
            ),
            "tool_selection_accuracy": average("tool_selection_accuracy"),
            "required_tool_recall": weighted_ratio(
                "required_tool_hits", "required_tool_count", empty=1.0
            ),
            "unexpected_tool_rate": weighted_ratio(
                "unexpected_tool_count", "observed_tool_count", empty=0.0
            ),
            "forbidden_tool_rate": weighted_ratio(
                "forbidden_tool_count", "observed_tool_count", empty=0.0
            ),
            "tool_argument_accuracy": average("tool_argument_accuracy"),
            "required_argument_accuracy": weighted_ratio(
                "required_argument_passes", "required_argument_count", empty=1.0
            ),
            "fact_accuracy": weighted_ratio(
                "expected_fact_passes",
                "expected_fact_count",
                empty=1.0,
                candidates=quality_evaluations,
            ),
            "expected_fact_accuracy": weighted_ratio(
                "expected_fact_passes",
                "expected_fact_count",
                empty=1.0,
                candidates=quality_evaluations,
            ),
            "escalation_accuracy": category_success("escalation"),
            "kb_answer_accuracy": category_success("kb_policy"),
            "order_status_accuracy": category_success("order_status"),
            "inventory_accuracy": category_success("inventory"),
            "return_flow_accuracy": category_success("returns"),
            "multi_turn_context_accuracy": category_success("multi_turn"),
            "insufficient_information_accuracy": category_success("unknown"),
            "completion_rate": (
                sum(bool(item["metrics"]["completion_success"]) for item in evaluations)
                / completed
                if completed
                else None
            ),
            "timeout_rate": (
                sum(
                    any(
                        error.get("type") == "timeout"
                        for error in record["execution"].get("errors", [])
                    )
                    for record in records
                )
                / completed
                if completed
                else None
            ),
            "agent_error_rate": (
                sum(bool(item["metrics"]["agent_error_case"]) for item in evaluations)
                / completed
                if completed
                else None
            ),
            "tool_error_rate": weighted_ratio(
                "tool_error_count", "observed_tool_count", empty=0.0
            ),
            "retry_rate": (
                sum(
                    int(record["execution"]["performance"].get("retry_count", 0)) > 0
                    for record in records
                )
                / completed
                if completed
                else None
            ),
            "malformed_output_rate": (
                sum(bool(item["metrics"]["malformed_output_case"]) for item in evaluations)
                / completed
                if completed
                else None
            ),
            "retrieval_failure_rate": weighted_ratio(
                "retrieval_failure_count", "retrieval_call_count", empty=0.0
            ),
        },
        "hallucination": {
            "factual_claim_count": claims,
            "supported_factual_claims": sum(
                item["metrics"]["supported_factual_claims"] for item in quality_evaluations
            ),
            "contradicted_claims": sum(
                item["metrics"]["contradicted_claims"] for item in quality_evaluations
            ),
            "unsupported_claims": sum(
                item["metrics"]["unsupported_claims"] for item in quality_evaluations
            ),
            "fabricated_entities_or_values": sum(
                item["metrics"]["fabricated_entities_or_values"]
                for item in quality_evaluations
            ),
            "hallucination_case_rate": (
                sum(bool(item["metrics"]["hallucination_case"]) for item in quality_evaluations)
                / len(quality_evaluations)
                if quality_evaluations
                else None
            ),
            "unsupported_claim_rate": unsupported / claims if claims else 0.0,
            "grounded_case_rate": (
                sum(bool(item["metrics"]["grounded_case"]) for item in quality_evaluations)
                / len(quality_evaluations)
                if quality_evaluations
                else None
            ),
            "scope": "deterministically extractable business values and identifiers",
        },
        "privacy_isolation": {
            "evaluated_cases": sum("isolation" in record["execution"] for record in records),
            "leakage_cases": sum(
                bool(item["metrics"]["leakage_observed"]) for item in evaluations
            ),
            "leakage_rate": (
                sum(bool(item["metrics"]["leakage_observed"]) for item in evaluations)
                / max(
                    1,
                    sum("isolation" in record["execution"] for record in records),
                )
                if any("isolation" in record["execution"] for record in records)
                else None
            ),
            "memory_isolation_pass_rate": (
                1.0
                - sum(bool(item["metrics"]["leakage_observed"]) for item in evaluations)
                / sum("isolation" in record["execution"] for record in records)
                if any("isolation" in record["execution"] for record in records)
                else None
            ),
        },
        "retrieval": {
            "evaluated_cases": len(measured_retrieval),
            "document_id_origin": "benchmark_derived_from_exact_returned_content",
            "recall_at_1": retrieval_average("recall_at_1"),
            "recall_at_3": retrieval_average("recall_at_3"),
            "recall_at_5": retrieval_average("recall_at_5"),
            "mrr": retrieval_average("mrr"),
            "ndcg": retrieval_average("ndcg"),
            "citation_precision": None,
            "citation_recall": None,
            "citation_status": "N/A_no_structured_citations",
        },
        "performance": {
            "total_case_latency_ms": latency_summary(latency_values),
            "model_latency_ms": latency_summary(model_latency_values),
            "model_latency_status": (
                "measured_from_ollama_response_metadata"
                if model_latency_values
                else "N/A_not_exposed"
            ),
            "queue_wait_ms": None,
            "queue_wait_status": "N/A_not_returned_by_wait_protocol",
            "tool_latency_ms": None,
            "tool_latency_status": "N/A_not_exposed",
            "retrieval_latency_ms": None,
            "retrieval_latency_status": "N/A_not_exposed",
            "time_to_first_token_ms": None,
            "time_to_first_token_status": "N/A_non_streaming_wait_protocol",
            "warmup_excluded_ms": 89168,
            "retry_count": {
                "total": sum(
                    int(record["execution"]["performance"].get("retry_count", 0))
                    for record in records
                ),
                "mean_per_case": (
                    statistics.fmean(
                        int(record["execution"]["performance"].get("retry_count", 0))
                        for record in records
                    )
                    if records
                    else None
                ),
            },
            "tool_call_count": {
                "total": sum(
                    int(record["execution"]["performance"].get("tool_call_count", 0))
                    for record in records
                ),
                "mean_per_case": (
                    statistics.fmean(
                        int(record["execution"]["performance"].get("tool_call_count", 0))
                        for record in records
                    )
                    if records
                    else None
                ),
            },
            "token_counts": {
                key: sum(usage.get(key, 0) for usage in measured_usages)
                for key in ("input_tokens", "output_tokens", "total_tokens")
            },
            "token_counts_status": (
                "measured_from_model_usage_metadata"
                if measured_usages
                else "N/A_not_exposed"
            ),
        },
        "errors": {
            "execution_errors": sum(
                bool(record["execution"].get("errors")) for record in records
            ),
            "timeouts": sum(
                any(
                    error.get("type") == "timeout"
                    for error in record["execution"].get("errors", [])
                )
                for record in records
            ),
        },
        "audit": {
            "pending_non_pass_cases": sum(
                item["audit_status"] == "pending" for item in evaluations
            ),
            "completed": False,
        },
    }
    if metadata["mode"] == "smoke" and latency_values:
        p99 = percentile(latency_values, 0.99) or max(latency_values)
        summary["recommended_primary_timeout_seconds"] = min(
            240, max(60, math.ceil(p99 * 2 / 1000))
        )
        summary["timeout_derivation"] = (
            "ceil(smoke p99 total-case latency × 2 / 1000), clamped to [60, 240] seconds"
        )
    if metadata["mode"] == "performance":
        throughput: dict[str, Any] = {}
        for batch in metadata.get("performance_batches", []):
            level_records = [
                record
                for record in records
                if record.get("concurrency") == batch["concurrency"]
            ]
            wall_seconds = batch.get("wall_seconds")
            level_latencies = [
                float(record["execution"]["performance"]["total_case_latency_ms"])
                for record in level_records
                if isinstance(
                    record.get("execution", {})
                    .get("performance", {})
                    .get("total_case_latency_ms"),
                    (int, float),
                )
            ]
            level_usages = [
                usage for record in level_records if (usage := measured_usage(record))
            ]
            throughput[f"C{batch['concurrency']}"] = {
                "label": "LOCAL BENCHMARK THROUGHPUT",
                "case_count": len(level_records),
                "completed_executions": len(level_records),
                "wall_seconds": wall_seconds,
                "cases_per_minute": (
                    len(level_records) * 60 / wall_seconds if wall_seconds else None
                ),
                "success_rate": (
                    sum(
                        record["evaluation"]["metrics"]["task_success"]
                        for record in level_records
                    )
                    / len(level_records)
                    if level_records
                    else None
                ),
                "mean_latency_ms": (
                    statistics.fmean(level_latencies) if level_latencies else None
                ),
                "p50_latency_ms": percentile(level_latencies, 0.50),
                "p95_latency_ms": percentile(level_latencies, 0.95),
                "token_counts": {
                    key: sum(usage.get(key, 0) for usage in level_usages)
                    for key in ("input_tokens", "output_tokens", "total_tokens")
                },
                "token_counts_status": (
                    "measured_from_model_usage_metadata"
                    if level_usages
                    else "N/A_not_exposed"
                ),
                "timeout_rate": (
                    sum(
                        any(
                            error.get("type") == "timeout"
                            for error in record["execution"].get("errors", [])
                        )
                        for record in level_records
                    )
                    / len(level_records)
                    if level_records
                    else None
                ),
                "error_rate": (
                    sum(
                        bool(record["execution"].get("errors"))
                        for record in level_records
                    )
                    / len(level_records)
                    if level_records
                    else None
                ),
            }
        summary["local_benchmark_throughput"] = throughput
    return summary


def execution_error(case: dict[str, Any], exc: Exception, elapsed_ms: int) -> dict[str, Any]:
    is_timeout = isinstance(exc, (TimeoutError, ExternalAgentError)) and (
        "timed out" in str(exc).lower() or "timeout" in str(exc).lower()
    )
    return {
        "case_id": case["id"],
        "thread_ids": [],
        "turns": [],
        "messages": [],
        "tool_calls": [],
        "retrievals": [],
        "final_response": "",
        "usage": None,
        "performance": {
            "queue_wait_ms": None,
            "queue_wait_status": "N/A",
            "external_agent_latency_ms": elapsed_ms,
            "model_latency_ms": None,
            "model_latency_status": "N/A_execution_failed",
            "tool_latency_ms": None,
            "tool_latency_status": "N/A",
            "retrieval_latency_ms": None,
            "retrieval_latency_status": "N/A",
            "total_case_latency_ms": elapsed_ms,
            "time_to_first_token_ms": None,
            "time_to_first_token_status": "N/A",
            "retry_count": 0,
            "tool_call_count": 0,
        },
        "errors": [
            {
                "type": "timeout" if is_timeout else "external_execution_error",
                "message": str(exc)[:1000],
                "transient": getattr(exc, "transient", False),
            }
        ],
    }


def run_one(
    adapter: LangGraphHttpAdapter,
    case: dict[str, Any],
    manifest: dict[str, Any],
    *,
    execution_id: str,
    mode: str,
    repeat: int | None = None,
    concurrency: int | None = None,
) -> dict[str, Any]:
    started_at = utc_now()
    started = time.perf_counter()
    try:
        if case.get("isolation"):
            execution = adapter.execute_isolation(case["id"], **case["isolation"])
        else:
            execution = adapter.execute(case["id"], case["turns"])
        evaluation = evaluate(case, execution, manifest)
    except Exception as exc:  # Persist infrastructure evidence instead of aborting the run.
        execution = execution_error(
            case, exc, round((time.perf_counter() - started) * 1000)
        )
        evaluation = evaluate(case, execution, manifest)
        evaluation["verdict"] = "block"
        evaluation["score"] = 0.0
        evaluation["reason_codes"] = list(
            dict.fromkeys(["EXTERNAL_EXECUTION_ERROR", *evaluation["reason_codes"]])
        )
        evaluation["findings"].insert(
            0,
            {
                "type": "infrastructure",
                "reason_code": "EXTERNAL_EXECUTION_ERROR",
                "severity": "block",
                "actual_claim": str(exc)[:1000],
                "expected_evidence": "Successful local LangGraph execution",
                "actual_evidence": execution["errors"],
                "source_reference": "adapter.py",
                "reason": "The external execution did not complete successfully.",
                "audit_classification": None,
            },
        )
    return {
        "schema_version": 1,
        "execution_id": execution_id,
        "mode": mode,
        "repeat": repeat,
        "concurrency": concurrency,
        "case": {
            "id": case["id"],
            "name": case["name"],
            "category": case["category"],
            "difficulty": case["difficulty"],
            "prompt": case["prompt"],
            "read_only": case["read_only"],
        },
        "started_at": started_at,
        "completed_at": utc_now(),
        "execution": execution,
        "evaluation": evaluation,
    }


def select_cases(
    cases: list[dict[str, Any]], args: argparse.Namespace, mode: str
) -> list[dict[str, Any]]:
    selected = cases
    if mode == "smoke":
        selected = [case for case in selected if case["smoke"]]
    elif mode == "stability":
        selected = [case for case in selected if case["stability"]]
    elif mode == "performance":
        selected = [case for case in selected if case["performance"]]
    if args.case:
        wanted = set(args.case)
        selected = [case for case in selected if case["id"] in wanted]
        missing = wanted - {case["id"] for case in selected}
        if missing:
            raise ValueError(f"Unknown or mode-ineligible case IDs: {sorted(missing)}")
    if args.category:
        selected = [
            case for case in selected if case["category"] in set(args.category)
        ]
    if args.difficulty:
        selected = [
            case for case in selected if case["difficulty"] in set(args.difficulty)
        ]
    if args.limit is not None:
        selected = selected[: args.limit]
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the real external LangGraph benchmark"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8123")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--case", action="append")
    parser.add_argument("--category", action="append")
    parser.add_argument(
        "--difficulty", action="append", choices=["easy", "medium", "hard"]
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--stability", action="store_true")
    parser.add_argument(
        "--performance",
        nargs="?",
        const="all",
        choices=["all", "C1", "C2", "C4"],
    )
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--retry-incomplete-execution-id",
        action="append",
        help="Stability-only recovery: quarantine and rerun one incomplete execution ID",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if sum(bool(item) for item in (args.smoke, args.stability, args.performance)) > 1:
        raise SystemExit(
            "Choose at most one of --smoke, --stability, or --performance"
        )
    mode = (
        "smoke"
        if args.smoke
        else "stability"
        if args.stability
        else "performance"
        if args.performance
        else "primary"
    )
    manifest = load_json(ROOT / "manifest.yaml")
    cases = load_json(ROOT / "cases.json")
    validation = validate(manifest, cases)
    if not validation["valid"]:
        raise SystemExit(
            "Manifest validation failed: " + "; ".join(validation["errors"])
        )
    selected = select_cases(cases, args, mode)
    if not selected:
        raise SystemExit("No cases selected")

    suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or RESULTS_ROOT / f"{suffix}-{mode}").resolve()
    records_path = output / "cases.jsonl"
    if output.exists() and not args.resume and records_path.exists():
        raise SystemExit(
            f"Output already has results; use --resume or a new --output: {output}"
        )
    output.mkdir(parents=True, exist_ok=True)
    existing = read_jsonl(records_path) if args.resume else []
    retry_ids = set(args.retry_incomplete_execution_id or [])
    if retry_ids:
        if mode != "stability" or not args.resume:
            raise SystemExit(
                "--retry-incomplete-execution-id requires --stability and --resume"
            )
        indexed = {record["execution_id"]: record for record in existing}
        missing_retry_ids = retry_ids - set(indexed)
        if missing_retry_ids:
            raise SystemExit(
                f"Cannot retry execution IDs absent from the result set: {sorted(missing_retry_ids)}"
            )
        completed_retry_ids = {
            execution_id
            for execution_id in retry_ids
            if indexed[execution_id]["evaluation"]["metrics"].get(
                "completion_success", False
            )
        }
        if completed_retry_ids:
            raise SystemExit(
                "Refusing to retry completed executions: "
                f"{sorted(completed_retry_ids)}"
            )
        quarantine_path = output / "excluded-infrastructure-attempts.jsonl"
        for execution_id in sorted(retry_ids):
            append_jsonl(
                quarantine_path,
                {
                    "excluded_from_stability": True,
                    "classification": "INFRASTRUCTURE_FAILURE",
                    "reason": "External graph returned no messages during local Ollama unavailability.",
                    "record": indexed[execution_id],
                },
            )
        existing = [
            record for record in existing if record["execution_id"] not in retry_ids
        ]
        atomic_jsonl(records_path, existing)
    completed_ids = {record["execution_id"] for record in existing}

    tool_map = load_json(ROOT / "tool-map.json")
    adapter = LangGraphHttpAdapter(
        args.base_url,
        timeout_seconds=args.timeout_seconds,
        document_signatures=tool_map["retrieval_documents"],
    )
    health = adapter.health()
    repetitions = (
        range(1, manifest["stability"]["repetitions"] + 1)
        if mode == "stability"
        else [None]
    )
    concurrency_levels = (
        [int(args.performance[1:])]
        if mode == "performance" and args.performance != "all"
        else manifest["performance"]["concurrency_levels"]
        if mode == "performance"
        else [None]
    )
    selected_count = len(selected) * len(list(repetitions)) * len(concurrency_levels)
    metadata_path = output / "run.json"
    if args.resume and metadata_path.exists():
        metadata = load_json(metadata_path)
        if metadata.get("mode") != mode:
            raise SystemExit("Resume mode does not match existing run metadata")
        metadata["status"] = "running"
        metadata["finished_at"] = None
    else:
        metadata = {
            "schema_version": 1,
            "run_id": str(uuid.uuid4()),
            "benchmark_id": manifest["benchmark_id"],
            "mode": mode,
            "started_at": utc_now(),
            "finished_at": None,
            "status": "running",
            "base_url": args.base_url,
            "timeout_seconds": args.timeout_seconds,
            "selected_case_ids": [case["id"] for case in selected],
            "selected_execution_count": selected_count,
            "upstream": manifest["upstream"],
            "model": manifest["model"],
            "health": health,
            "warmup_excluded_ms": 89168,
            "performance_batches": [],
            "resume_supported": True,
        }
    atomic_json(metadata_path, metadata)

    def persist(record: dict[str, Any]) -> None:
        append_jsonl(records_path, record)
        existing.append(record)
        atomic_json(output / "summary.json", build_summary(existing, metadata))
        verdict = record["evaluation"]["verdict"].upper()
        latency = record["execution"]["performance"]["total_case_latency_ms"]
        print(f"{record['execution_id']}: {verdict} ({latency} ms)", flush=True)

    if mode == "performance":
        for concurrency in concurrency_levels:
            work = [
                (case, f"{case['id']}::performance::C{concurrency}")
                for case in selected
                if f"{case['id']}::performance::C{concurrency}"
                not in completed_ids
            ]
            batch_started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {
                    executor.submit(
                        run_one,
                        adapter,
                        case,
                        manifest,
                        execution_id=execution_id,
                        mode=mode,
                        concurrency=concurrency,
                    ): execution_id
                    for case, execution_id in work
                }
                for future in as_completed(futures):
                    persist(future.result())
            batch = {
                "concurrency": concurrency,
                "completed_in_this_process": len(work),
                "wall_seconds": time.perf_counter() - batch_started,
                "resumed_partial_batch": len(work) != len(selected),
            }
            metadata["performance_batches"] = [
                item
                for item in metadata["performance_batches"]
                if item["concurrency"] != concurrency
            ] + [batch]
            atomic_json(metadata_path, metadata)
    else:
        for repeat in repetitions:
            for case in selected:
                execution_id = (
                    f"{case['id']}::stability::{repeat}"
                    if repeat is not None
                    else case["id"]
                )
                if execution_id in completed_ids:
                    print(f"{execution_id}: SKIP (already complete)", flush=True)
                    continue
                persist(
                    run_one(
                        adapter,
                        case,
                        manifest,
                        execution_id=execution_id,
                        mode=mode,
                        repeat=repeat,
                    )
                )

    metadata["finished_at"] = utc_now()
    metadata["status"] = "completed"
    atomic_json(metadata_path, metadata)
    atomic_json(output / "summary.json", build_summary(existing, metadata))
    print(f"RESULT_DIR={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
