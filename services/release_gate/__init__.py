"""Release gating: compare a candidate run against an approved baseline, decide.

The product thesis ends with *"compare against approved baseline → PASS / WARN / BLOCK"*.
Until now the repository produced run verdicts but had no gate that reads a candidate and
a baseline and returns a release decision, which is the artifact CI actually needs.

Two properties are deliberate:

* the decision is **data in, data out** — two summaries and a threshold set, no network,
  no database, no model;
* a critical security failure is a **BLOCK regardless of score**, so no amount of quality
  improvement elsewhere can buy its way past a safety regression.
"""

from __future__ import annotations

from services.release_gate.gate import (
    GATE_REASONS,
    GateDecision,
    GateThresholds,
    evaluate_release,
)

__all__ = ["GATE_REASONS", "GateDecision", "GateThresholds", "evaluate_release"]
