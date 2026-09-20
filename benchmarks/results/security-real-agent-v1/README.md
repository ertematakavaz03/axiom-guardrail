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

## Run index

| Run | Target | Profile | Attack success | Notes |
|---|---|---|---:|---|
| `20260919-pilot` … `20260919-pilot-3` | raw pinned agent | pilot (12) | — | methodology development; see the defect ledger |
| `20260919-full-2` | **raw pinned agent** | full (88) | **60.47%** | The immutable raw baseline. Nothing enforces anything. |
| `20260920-phase4-hardened-1` | **Axiom-mediated agent** | full (88) | **9.09%** | Same corpus, model, prompt and tools; tool execution routed through the Phase 4 enforcement boundary. |

Both full runs are preserved. Neither supersedes the other: they measure two different
*deployment architectures* against one frozen methodology, and the comparison is only
meaningful while both sets of bytes exist.

`prevention_rate` is `null` in every run of this suite by design: the frozen suite has no
trusted gateway receipts. In `20260920-phase4-hardened-1` this is a reporting artifact
rather than a statement of fact — prevention demonstrably occurred (59 tool calls were
denied before execution, visible in `cases.jsonl` as `status: "error"` with a policy
reason code), but the frozen evaluator has no channel through which to record it and still
reports `prevention_status: "N/A_no_host_owned_executor"`. The methodology was deliberately
left unchanged rather than taught about the new target.
