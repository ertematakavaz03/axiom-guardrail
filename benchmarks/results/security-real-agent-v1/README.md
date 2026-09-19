# security-real-agent-v1 results

Raw artifacts from the real-agent adversarial benchmark. Each run directory contains:

| File | Contents |
|---|---|
| `cases.jsonl` | One `RealAgentCaseResult` per line, including the raw trace |
| `run.json` | Benchmark, detector, shadow-policy and report versions; corpus digest; upstream pin; model identity; runtime counters |
| `summary.json` | Metrics, recomputable from `cases.jsonl` alone |
| `artifacts-sha256.json` | SHA-256 of the three files above |

Recompute the metrics without re-executing the model:

```sh
python -c "from pathlib import Path; from demos.security_real_agent.report import aggregate, load_results; import json; print(json.dumps(aggregate(load_results(Path('benchmarks/results/security-real-agent-v1/<run>/cases.jsonl'))), indent=2))"
```

Artifacts are immutable once written. A corrected expectation is recorded as a new run
directory plus a dated defect note, never by editing a previous run.

`prevention_rate` is `null` in every run of this suite by design: the target is a
third-party agent that owns its own tool layer, so no trusted gateway receipt can exist.

## Runs

**`20260919-full-2` is the primary reportable baseline.** The three pilots are retained as
methodology evidence, not as results: each is superseded, and quoting a pilot rate as a
benchmark figure is a reporting error.

| Run | Profile | Cases | Role |
|---|---|---|---|
| `20260919-pilot` | pilot | 12 | superseded. Pre-correction evidence for defects D-001...D-007 (runtime provenance, prediction-vs-adjudication conflation, digest prefix overstatement). |
| `20260919-pilot-2` | pilot | 12 | superseded. First run with markers configured; exposed D-012 (a marker miss scored as a defence) and D-013 (marker provenance flattened to nulls). Preserved as evidence of the flawed classifier state. |
| `20260919-pilot-3` | pilot | 12 | superseded. First run under the corrected classifier. Reported `attack_success_rate` 0.60 on a five-case denominator; reproduced the headline signal only. |
| **`20260919-full-2`** | **full** | **88** | **PRIMARY BASELINE.** 48 attacks / 40 benign controls, 43 scorable attacks, 0 runtime failures, `attack_success_rate` 0.6047, benign controls 40/40 safe. Full report: [docs/security-real-agent-v1-baseline.md](../../../docs/security-real-agent-v1-baseline.md) |

`20260919-full-1` does not exist as a result: that attempt blocked indefinitely on a chunked
LangGraph response and produced no artifacts. It is recorded as D-014 in the
[defect ledger](../../../docs/security-real-agent-v1-defects.md), and no results were
manufactured for it.

A completed run also retains `checkpoint/`, whose `state.json` carries the methodology
fingerprint the run executed under -- benchmark id, profile, corpus digest, schema version,
extraction evidence policy, detector and shadow versions, marker provenance, case order,
timeout policy and the methodology commit. A run still in flight is marked
`PARTIAL_IN_PROGRESS_NOT_A_BENCHMARK_RESULT`; the four final artifacts appear only once the
run reaches its terminal state, so a partial checkpoint can never be read as a result.
