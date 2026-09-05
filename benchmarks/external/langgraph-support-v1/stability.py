from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runner import atomic_json, latency_summary, read_jsonl


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def analyze(records: list[dict[str, Any]], expected_repetitions: int = 3) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["case"]["id"]].append(record)
    incomplete = {
        case_id: len(case_records)
        for case_id, case_records in grouped.items()
        if len(case_records) != expected_repetitions
    }
    if incomplete:
        raise ValueError(f"Incomplete stability repetitions: {incomplete}")

    cases: list[dict[str, Any]] = []
    for case_id in sorted(grouped):
        case_records = sorted(grouped[case_id], key=lambda item: int(item["repeat"]))
        verdicts = [record["evaluation"]["verdict"] for record in case_records]
        tool_selections = [
            [call["name"] for call in record["execution"]["tool_calls"]]
            for record in case_records
        ]
        arguments = [
            [
                {"name": call["name"], "arguments": call["arguments"]}
                for call in record["execution"]["tool_calls"]
            ]
            for record in case_records
        ]
        fact_outcomes = [
            {
                "passes": record["evaluation"]["metrics"]["expected_fact_passes"],
                "count": record["evaluation"]["metrics"]["expected_fact_count"],
            }
            for record in case_records
        ]
        hallucination_outcomes = [
            {
                key: record["evaluation"]["metrics"][key]
                for key in (
                    "supported_factual_claims",
                    "contradicted_claims",
                    "unsupported_claims",
                    "fabricated_entities_or_values",
                    "grounded_case",
                )
            }
            for record in case_records
        ]
        latencies = [
            float(record["execution"]["performance"]["total_case_latency_ms"])
            for record in case_records
        ]
        mean_latency = statistics.fmean(latencies)
        cases.append(
            {
                "case_id": case_id,
                "verdicts": verdicts,
                "tool_selections": tool_selections,
                "arguments": arguments,
                "fact_outcomes": fact_outcomes,
                "hallucination_outcomes": hallucination_outcomes,
                "verdict_consistent": len(set(verdicts)) == 1,
                "tool_selection_consistent": len({canonical(item) for item in tool_selections})
                == 1,
                "argument_consistent": len({canonical(item) for item in arguments}) == 1,
                "fact_consistent": len({canonical(item) for item in fact_outcomes}) == 1,
                "hallucination_consistent": len(
                    {canonical(item) for item in hallucination_outcomes}
                )
                == 1,
                "latency_ms": latency_summary(latencies),
                "latency_standard_deviation_ms": statistics.stdev(latencies),
                "latency_coefficient_of_variation": (
                    statistics.stdev(latencies) / mean_latency if mean_latency else None
                ),
            }
        )

    def consistency_rate(field: str) -> float:
        return sum(bool(case[field]) for case in cases) / len(cases)

    all_latencies = [
        float(record["execution"]["performance"]["total_case_latency_ms"])
        for record in records
    ]
    mean_latency = statistics.fmean(all_latencies)
    return {
        "schema_version": 1,
        "benchmark_id": "langgraph-support-v1",
        "analyzed_at": datetime.now(UTC).isoformat(),
        "case_count": len(cases),
        "repetitions_per_case": expected_repetitions,
        "execution_count": len(records),
        "separate_from_primary_score": True,
        "consistency": {
            "verdict_consistency": consistency_rate("verdict_consistent"),
            "tool_selection_consistency": consistency_rate("tool_selection_consistent"),
            "argument_consistency": consistency_rate("argument_consistent"),
            "fact_consistency": consistency_rate("fact_consistent"),
            "hallucination_consistency": consistency_rate("hallucination_consistent"),
        },
        "latency_variability": {
            "latency_ms": latency_summary(all_latencies),
            "standard_deviation_ms": statistics.stdev(all_latencies),
            "coefficient_of_variation": (
                statistics.stdev(all_latencies) / mean_latency if mean_latency else None
            ),
        },
        "verdict_counts": dict(
            sorted(Counter(record["evaluation"]["verdict"] for record in records).items())
        ),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze 20x3 benchmark stability")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(read_jsonl(args.input / "cases.jsonl"))
    output = args.output or args.input / "stability-analysis.json"
    atomic_json(output, result)
    print(json.dumps({**result["consistency"], **result["latency_variability"]}, indent=2))
    print(f"OUTPUT={output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
