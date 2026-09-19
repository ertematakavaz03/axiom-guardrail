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

| Run | Profile | Cases | Status |
|---|---|---|---|
| `20260919-pilot` | pilot | 12 | superseded; pre-correction evidence for D-001...D-007 |
| `20260919-pilot-2` | pilot | 12 | superseded; evidence of the D-012 classifier defect and D-013 provenance loss |
| `20260919-pilot-3` | pilot | 12 | superseded; first run under the corrected classifier |
| `20260919-full-2` | full | **88** | **canonical baseline** -- see `docs/security-real-agent-v1-baseline.md` |

`20260919-full-1` does not exist: that attempt blocked on a chunked LangGraph response and
produced no artifacts. It is recorded as D-014 in the defect ledger, and no results were
manufactured for it.

A completed run also retains `checkpoint/`, whose `state.json` carries the methodology
fingerprint the run executed under -- benchmark id, profile, corpus digest, schema version,
extraction evidence policy, detector and shadow versions, marker provenance, case order,
timeout policy and the methodology commit. A run still in flight is marked
`PARTIAL_IN_PROGRESS_NOT_A_BENCHMARK_RESULT`; the four final artifacts appear only once the
run reaches its terminal state, so a partial checkpoint can never be read as a result.
