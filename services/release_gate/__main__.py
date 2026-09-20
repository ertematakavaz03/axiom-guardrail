"""CLI for the release gate, so a CI step is one command and one exit code.

    python -m services.release_gate \
        --candidate out/summary.json \
        --baseline benchmarks/baselines/security-real-agent-v1.json \
        --thresholds config/release-gate.json \
        --output gate.json

Exits 1 on ``BLOCK`` and 0 otherwise, prints the decision as JSON on stdout, and writes
the same object to ``--output`` when given so a pipeline can attach it as an artifact.

A missing baseline is reported and blocks; it is never silently treated as a first run.
Use ``--allow-missing-baseline`` to bootstrap deliberately, which is recorded in the
decision rather than hidden.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from services.release_gate.gate import (
    REASON_BASELINE_MISSING,
    GateThresholds,
    evaluate_release,
)


def _load(path: Path | None) -> dict[str, Any] | None:
    """Read a JSON object, dropping ``_``-prefixed keys.

    Those carry provenance — which run a baseline came from, why a threshold is what it
    is — and belong in the committed file next to the numbers they explain. Stripping
    them here keeps that documentation out of the strict models below, so a comment can
    never be mistaken for a metric or a threshold.
    """
    if path is None or not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    return {key: value for key, value in data.items() if not key.startswith("_")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Axiom Guardrail release gate")
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--thresholds", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--allow-missing-baseline",
        action="store_true",
        help="bootstrap a new suite: downgrade a missing baseline from BLOCK to WARN",
    )
    args = parser.parse_args(argv)

    candidate = _load(args.candidate)
    if candidate is None:
        print(json.dumps({"verdict": "BLOCK", "reasons": ["GATE_CANDIDATE_UNREADABLE"]}))
        return 1

    thresholds_data = _load(args.thresholds) or {}
    decision = evaluate_release(candidate, _load(args.baseline), GateThresholds(**thresholds_data))

    if (
        args.allow_missing_baseline
        and decision.reasons == [REASON_BASELINE_MISSING]
        and decision.verdict == "BLOCK"
    ):
        # Only when it is the *sole* reason: bootstrapping must not wave through a run
        # that also regressed on something the gate could measure.
        decision = decision.model_copy(update={"verdict": "WARN"})

    payload = decision.model_dump(mode="json")
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return decision.exit_code()


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
