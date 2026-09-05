# Axiom Guardrail External Benchmark #1

Generated: 2026-09-05T00:25:48.238968+00:00

## Reproducibility verdict

Complete. The real pinned external LangGraph agent was evaluated without source modification. The primary score contains exactly 100 unique cases at concurrency 1; throughput and stability runs are reported separately.

## Benchmark purpose

This benchmark measures whether Axiom can ingest, preserve, deterministically evaluate, audit, and present evidence from an independently healthy third-party tool-using agent. It also reports the external agent's observed task, tool, grounding, isolation, reliability, and local performance outcomes without implying endorsement, affiliation, certification, or production readiness.

## External source and protected baseline

- Repository: `https://github.com/aperritano/langgraph-customer-support-agent`
- Pinned SHA: `64dea789d7b59ae6a57470091d3dbf4ba43fe7cb` (detached checkout)
- License: MIT declared in `pyproject.toml`; standalone license file present: False
- Axiom baseline: `v0.2.0` / `a5f73a8a506c54cc509d1208859f710a9c08331b`
- Axiom branch: `benchmark/langgraph-support-v1`

## External agent architecture and tool surface

The pinned application is a LangGraph ReAct loop: START → ChatOllama agent → conditional tool routing → LangGraph ToolNode → agent, terminating when the model returns no tool call. Conversation messages are held in graph state; the local LangGraph server supplies thread/checkpoint handling. The graph binds llama3.1 at temperature 0 with a 4,096-token context and a 512-token generation cap.

| Tool | Observed role | Side effect in pinned demo |
| --- | --- | --- |
| `list_available_functions` | read | False |
| `send_greeting` | read | False |
| `search_vector_knowledge_base` | retrieval | False |
| `get_order_status` | read | False |
| `list_orders` | read | False |
| `initiate_return` | simulated_action | simulated_non_persistent |
| `check_product_availability` | read | False |
| `escalate_to_human` | simulated_action | local_log_only |

## Environment

- OS: Microsoft Windows 11 Home Single Language 10.0.26200 (64 bit)
- CPU: 12th Gen Intel(R) Core(TM) i7-12700H (14 physical / 20 logical)
- Memory: 31.68 GiB
- GPU: NVIDIA GeForce RTX 3060 Laptop GPU (6144 MiB), driver 592.82
- Python: 3.12.10 in isolated `.venv312`
- Ollama: 0.33.2
- Model: `llama3.1:latest`; digest `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e`; 8.0B Q4_K_M
- Excluded warm-up: 89,168 ms

Upstream verification: 53 passed, 0 failed (46 non-LLM; 7 Ollama-dependent).

## Methodology

The benchmark drives the pinned graph through its real local LangGraph HTTP protocol and local Ollama model. Each primary case receives an independent thread except explicitly declared multi-turn cases. The adapter records messages, tool calls/arguments/results, final responses, thread IDs, and observable model metadata without invoking Axiom tools. Source-backed deterministic rules evaluate required tools, arguments, expected facts, extractable factual claims, retrieval, and synthetic-canary isolation. No paid LLM judge is used. Every non-pass result is audited before aggregation.

### Raw execution versus final evaluation

`20260904-primary/cases.jsonl` is the immutable RAW EXECUTION artifact produced by 100 real model calls. The external model was not called during correction. FINAL EVALUATION was recomputed from those frozen traces into `20260904-primary-audit/audited-cases.jsonl`; its audit and summary are separate hashed artifacts. The raw execution SHA-256 is unchanged across re-evaluation.

## Dataset

Validation status: `True`; primary cases: 100; smoke: 12; safe performance: 20; stability: 20; canaries: 5.

| Category | Cases |
| --- | ---: |
| escalation | 10 |
| inventory | 10 |
| kb_policy | 15 |
| multi_turn | 10 |
| no_tool | 5 |
| order_status | 15 |
| privacy_isolation | 5 |
| returns | 15 |
| unknown | 5 |
| upstream_control | 10 |

Difficulty: easy=37, hard=24, medium=39.

## Smoke audit

All 12 smoke cases were persisted and audited. Final audited verdicts: 6 pass, 0 warn, 6 block. Smoke accepted: `True`. The six remaining blocks were classified as trace-supported `REAL_AGENT_FAILURE`; none were infrastructure, adapter, expectation, or unresolved evaluator failures.

## Primary 100-case results

Verdicts: 57 pass, 0 warn, 43 block. Audited validated agent-failure cases: 42; unresolved: 0.

Agent-quality aggregates use 99 eligible cases. 1 case was excluded because its expected branch was impossible under the pinned tool's substring behavior; its raw execution and operational/tool evidence remain retained.

Final non-pass case taxonomy: BENCHMARK_EXPECTATION_BUG=1, REAL_AGENT_FAILURE=42, MODEL_NONDETERMINISM=2, EVALUATOR_FALSE_POSITIVE=0, ADAPTER_BUG=0, INFRASTRUCTURE_FAILURE=0, UNRESOLVED=0. Model nondeterminism is derived only from the separate stability run and does not alter primary quality.

### Quality and tool metrics

| Metric | Result |
| --- | ---: |
| Task Success Rate | 58.59% |
| Tool Selection Accuracy | 85.00% |
| Required Tool Recall | 100.00% |
| Unexpected Tool Rate | 0.00% |
| Forbidden Tool Rate | 0.00% |
| Tool Argument Accuracy | 99.67% |
| Required Argument Accuracy | 100.00% |
| Escalation Accuracy | 40.00% |
| Fact Accuracy | 59.79% |
| KB Answer Accuracy | 46.67% |
| Order Status Accuracy | 73.33% |
| Inventory Accuracy | 100.00% |
| Return Flow Accuracy | 13.33% |
| Multi-turn Context Accuracy | 70.00% |
| Insufficient-Information Accuracy | 50.00% |

### Hallucination and groundedness

Claim scope: deterministically extractable business values and identifiers. No paid LLM judge was used.

| Metric | Result |
| --- | ---: |
| Factual Claim Count | 89 |
| Supported Factual Claim Count | 84 |
| Contradicted Claim Count | 3 |
| Unsupported Claim Count | 2 |
| Fabricated Entity/Value Count | 0 |
| Hallucination Case Rate | 2.02% |
| Unsupported Claim Rate | 2.25% |
| Grounded Case Rate | 97.98% |

### Privacy and isolation

Synthetic canary cases: 5; canary leakage count: 0; cross-case leakage rate: 0.00%; memory isolation pass rate: 100.00%. This is an observed benchmark-isolation result, not a privacy certification.

### Retrieval

Measured cases: 17; Recall@1/3/5: 0.235 / 0.235 / 0.235; MRR: 0.235; nDCG: 0.235. IDs are benchmark-derived from exact returned content. Citation precision/recall: N/A / N/A because the agent emits no structured citations.

### Reliability, latency, and observable telemetry

Execution errors: 0; timeouts: 0; retries: 0; tool calls: 106; tokens: 504597 total (496857 input / 7740 output).

| Reliability metric | Result |
| --- | ---: |
| Completion Rate | 100.00% |
| Timeout Rate | 0.00% |
| Agent Error Rate | 0.00% |
| Tool Error Rate (per observed call) | 0.94% |
| Benchmark Retry Rate | 0.00% |
| Malformed Output Rate | 16.00% |
| Retrieval Failure Rate (per retrieval call) | 25.00% |

| Timing | Mean | Median | p50 | p90 | p95 | p99 | Min | Max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Total case latency (ms) | 28053.29 | 22702.50 | 22702.50 | 45964.40 | 46687.70 | 55637.09 | 5621.00 | 142568.00 |
| Model latency (ms) | 24576.97 | 20176.74 | 20176.74 | 44255.51 | 45029.77 | 45648.13 | 4698.70 | 47079.33 |

Queue wait: N/A; tool latency: N/A; retrieval latency: N/A; TTFT: N/A. These remain N/A because the non-streaming LangGraph wait protocol did not expose them.

## LOCAL BENCHMARK THROUGHPUT

This is a local laptop benchmark, not a production-capacity claim.

| Concurrency | Completed | Cases/min | Success | Mean latency (ms) | p50 (ms) | p95 (ms) | Tokens | Timeout | Error |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| C1 | 20 | 2.62 | 80.00% | 22886.60 | 21030.00 | 33834.10 | 93200 | 0.00% | 0.00% |
| C2 | 20 | 3.29 | 80.00% | 35546.60 | 31671.50 | 51271.95 | 93184 | 0.00% | 0.00% |
| C4 | 20 | 3.29 | 85.00% | 68778.95 | 59869.50 | 109083.05 | 93192 | 0.00% | 0.00% |

## Stability (20 cases × 3 repeats)

Executions: 60; excluded from primary score: `True`.
A transient local Ollama-unavailable attempt was quarantined and its single execution ID rerun; excluded infrastructure attempts: 1. The canonical stability set contains 60 unique, completed executions with no execution errors.

| Metric | Result |
| --- | ---: |
| Verdict Consistency | 95.00% |
| Tool Selection Consistency | 100.00% |
| Argument Consistency | 100.00% |
| Fact Consistency | 95.00% |
| Hallucination Consistency | 90.00% |
| Latency coefficient of variation | 77.39% |

### Model nondeterminism

2 of 20 stability cases changed at least one audited verdict/tool/argument/fact/hallucination outcome across repeats. These variations are reported separately and do not change the primary score.

- `order-789012` — verdict=False, tools=True, arguments=True, facts=False, hallucination=False
- `return-789012-defective` — verdict=True, tools=True, arguments=True, facts=True, hallucination=False

## Validated external-agent failures

Only audited `REAL_AGENT_FAILURE` cases are counted below. Semantic failures were not retried.

- `control-international-shipping` — INSUFFICIENT_INFORMATION_NOT_ACKNOWLEDGED
- `control-payment-methods` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `control-return-defective-123456` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `control-return-policy` — EXPECTED_FACT_MISSING
- `control-vague-order` — INSUFFICIENT_INFORMATION_NOT_ACKNOWLEDGED, MALFORMED_OUTPUT
- `escalation-angry` — EXPECTED_FACT_MISSING
- `escalation-exception` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `escalation-human-now` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `escalation-manual-review` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `escalation-specialist` — EXPECTED_FACT_MISSING
- `escalation-supervisor` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `kb-express-cost` — EXPECTED_FACT_MISSING, CONTRADICTED_FACTUAL_CLAIM, UNSUPPORTED_FACTUAL_CLAIM
- `kb-overnight-cost` — EXPECTED_FACT_MISSING
- `kb-overnight-time` — EXPECTED_FACT_MISSING, CONTRADICTED_FACTUAL_CLAIM
- `kb-payment-security` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `kb-return-condition` — EXPECTED_FACT_MISSING
- `kb-return-fee` — EXPECTED_FACT_MISSING
- `kb-return-window` — EXPECTED_FACT_MISSING
- `kb-standard-time` — TOOL_ARGUMENT_SCHEMA_MISMATCH
- `kb-warranty-period` — EXPECTED_FACT_MISSING
- `multi-order-345678` — EXPECTED_FACT_MISSING
- `multi-order-456789` — EXPECTED_FACT_MISSING
- `multi-order-567890` — EXPECTED_FACT_MISSING
- `order-667788` — EXPECTED_FACT_MISSING
- `order-678901` — EXPECTED_FACT_MISSING
- `order-789012` — EXPECTED_FACT_MISSING
- `order-901234` — EXPECTED_FACT_MISSING
- `return-001122-damaged` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `return-111222-wrong_item` — EXPECTED_FACT_MISSING
- `return-112233-wrong_item` — EXPECTED_FACT_MISSING
- `return-222333-broken` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `return-223344-broken` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `return-334455-defective` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `return-445566-changed_mind` — EXPECTED_FACT_MISSING
- `return-667788-wrong_item` — EXPECTED_FACT_MISSING
- `return-778899-broken` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `return-789012-defective` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `return-889900-defective` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT
- `return-890123-changed_mind` — EXPECTED_FACT_MISSING
- `return-990011-changed_mind` — EXPECTED_FACT_MISSING
- `unknown-international` — INSUFFICIENT_INFORMATION_NOT_ACKNOWLEDGED
- `unknown-order` — EXPECTED_FACT_MISSING, MALFORMED_OUTPUT

## False positives discovered and corrected

Correction records: EVALUATOR_FALSE_POSITIVE=11, BENCHMARK_EXPECTATION_BUG=2, ADAPTER_BUG=1. These discovered defects were corrected before final aggregation and do not count as validated external-agent failures.

- Capability-menu examples were treated as asserted order and inventory facts. Correction: Exclude numbered capability-menu entries and parenthetical examples from deterministic claim extraction. (cases: return-890123-changed_mind).
- The supported low-stock paraphrase 'only 1 unit remaining in stock' was treated as a contradictory IN_STOCK state. Correction: Match low-stock quantity paraphrases and suppress the nested IN_STOCK token in that exact scarcity construction. (cases: inventory-webcam).
- Pydantic-coercible numeric strings accepted by the live tool boundary were reported as argument schema mismatches. Correction: Accept only parseable integer/float strings where the pinned runtime performs safe numeric coercion. (cases: control-return-policy, kb-express-cost).
- A dollar threshold was compared as an exact price, and unsupported clock-time business claims were outside the deterministic claim scope. Correction: Classify under/over dollar values as thresholds and extract AM/PM clock-time claims. (cases: kb-express-cost).
- Warning reason codes could precede blocking correctness codes. Correction: Sort reason codes by safety/correctness precedence with warnings last. (cases: control-return-policy, kb-express-cost).
- The source-equivalent phrase 'defects in materials and workmanship' was rejected for the warranty's manufacturing-defect coverage. Correction: Canonicalize this narrowly audited warranty paraphrase to the source term before matching. (cases: kb-warranty-coverage).
- The word 'delivered' in an expected-delivery date was treated as a current DELIVERED status claim. Correction: Exclude delivered-status extraction when the term is governed by an expected/scheduled-to-be construction. (cases: order-112233, order-123456, order-678901, order-890123, order-901234).
- Availability wording followed by 'only 3 units remaining' was treated as contradicting LOW_STOCK. Correction: Treat the quantity-qualified availability construction as low-stock semantics rather than a separate IN_STOCK state. (cases: inventory-headphones).
- Availability cases required the literal digit zero even though 'out of stock' deterministically communicates zero available units. Correction: Accept the source-backed OUT_OF_STOCK state as satisfying the zero-availability fact, and ignore prospective 'back in stock' wording as a current-state claim. (cases: inventory-mouse, inventory-ergonomic-mouse).
- Backtick-formatted capability-menu examples were treated as asserted inventory facts. Correction: Exclude both bold and backtick numbered capability-menu entries from factual-claim extraction. (cases: return-112233-wrong_item, return-667788-wrong_item, return-990011-changed_mind).
- The phrase 'within the next hour' was marked unsupported against tool evidence stating 'within 1 hour'. Correction: Canonicalize this exact duration paraphrase before same-case evidence matching. (cases: return-556677-damaged).
- Generic Authorization text redaction also masked synthetic 'Return Authorization' identifiers in tool results. Correction: Continue redacting HTTP Authorization values while preserving the upstream mock RMA identifier label for future traces. (cases: return-*).
- An explicit 'no specific information' acknowledgement was not recognized as insufficient-information handling. Correction: Add that exact deterministic acknowledgement phrase to the unknown-response markers. (cases: unknown-cancellation).
- The policy-exception case rejected the semantically exact reason value 'requires_manual_review' even though the generated suite uses it for manual escalation. Correction: Add the narrow source-compatible reason alias to that case's deterministic expected arguments. (cases: escalation-exception).

## Infrastructure and observability limitations

Primary infrastructure failures: 0; performance errors: 0; excluded stability startup attempts: 1. Model duration and token usage are observable from Ollama response metadata. Per-tool latency, retrieval latency, internal queue wait, streaming TTFT, and structured citations are not exposed and remain N/A. Retrieval document IDs are benchmark-derived. The upstream actions are mock/simulated; generated RMA/ticket IDs are process-dependent; model outputs may vary despite temperature 0; and this single-machine test does not establish production capacity.

### External agent limitations

The pinned agent uses mock orders/inventory and a local vector store, while return and escalation actions are simulated. Retrieval can return semantically adjacent but irrelevant chunks; the model can emit raw function syntax or a capability menu instead of a user-facing answer; and temperature 0 does not eliminate backend/model nondeterminism. These are observed properties of this pinned demo, not claims about LangGraph or Ollama generally.

## Observational versus preventive enforcement

This benchmark observes the external agent after its own graph has selected and executed tools. Axiom records and evaluates those traces; it does not prevent, approve, sandbox, or replay the upstream calls. Accordingly, these results demonstrate observational evaluation, not preventive ToolGateway enforcement.

## Axiom product integration and limitations

Imported through the existing model: `External Benchmarks → LangGraph Customer Support Agent → External LangGraph Support Benchmark v1 → Run a19a9450-3a4e-4b20-b78f-21eaaf3ffc9a → Case → Trace`. Dashboard: `http://localhost:3000/runs/a19a9450-3a4e-4b20-b78f-21eaaf3ffc9a`. The import reuses Project, AgentVersion, TestSuite, Scenario, Run, CaseResult, Trace, and EvalResult; it does not create a standalone benchmark UI and does not replay already-executed external tools through Axiom's ToolGateway. Existing cards display their native metric subset; the complete audited metric payload is retained in the run snapshot/metrics and raw report artifacts.

Importer idempotency verified: `True`; resource counts matched expected counts on both imports: `True`. Axiom's current external-benchmark view is evidence and audit presentation, not live upstream call interception, a privacy certification, or a capacity-planning system.

## Reproduction commands

```powershell
python benchmarks/external/langgraph-support-v1/validator.py
python benchmarks/external/langgraph-support-v1/runner.py --output <new-primary-output-dir> --timeout-seconds 105
python benchmarks/external/langgraph-support-v1/runner.py --performance all --output <new-performance-output-dir> --timeout-seconds 105
python benchmarks/external/langgraph-support-v1/runner.py --stability --output <new-stability-output-dir> --timeout-seconds 105
python benchmarks/external/langgraph-support-v1/stability.py --input benchmarks/results/langgraph-support-v1/20260904-stability
python benchmarks/external/langgraph-support-v1/audit.py --input benchmarks/results/langgraph-support-v1/20260904-primary --corrections benchmarks/external/langgraph-support-v1/smoke-corrections.json --overrides benchmarks/external/langgraph-support-v1/primary-audit-overrides.json --output <new-audit-output-dir>
```

Use `--resume` with the same output directory after an interruption; completed execution IDs are skipped.

## Provenance hashes

- `pyproject.toml`: `3273e2f21fdbf22e924736b883a7025f1f719000a96d492b44afdd43f4648d90`
- `langgraph.json`: `e72a1c85f78328bc4945adac9a3c45a54b518df83217a19e9f0059cb40a49d87`
- `data/knowledge_base.json`: `64be6c46b62257ebab4704348f78c73c065fd635dc16263ff61779d0b6be2e9b`
- `src/support_agent/agent.py`: `370de2c35a57d751149a1012f6f13bd26e070c6aa0d4a58f016ec6145f5d5721`
- `src/support_agent/tools.py`: `4897a3a2f88c7fb4cb931a0b302a573c2c1f17b80dbea6034a85e5bfb700f0ad`
- `src/support_agent/vector_store.py`: `e54ca817557bb3893f574c28522aa2d7bee77cf54c10610dfad02685d1a926fa`
- `benchmarks/external/langgraph-support-v1/manifest.yaml`: `4731685942ca85a1dfc7659c2ec21726b706edc046453e62a4665f7c66098942`
- `benchmarks/external/langgraph-support-v1/cases.json`: `dc1b889a59a4c52ff45c78225c9b58defbc3692a84008d7eafe841b3878ba25d`
- `benchmarks/results/langgraph-support-v1/20260904-primary/cases.jsonl`: `8d8a14eab50d79c9eed2af9dc9d3db4a5f4c9d7450149a07d88581180a423e5b`
- `benchmarks/results/langgraph-support-v1/20260904-primary-audit/audited-cases.jsonl`: `ef4ad3e699fd7f0c90f3dec5a2530c3b0031d7ebd37635eb074646059ec4fee3`
- `benchmarks/results/langgraph-support-v1/20260904-primary-audit/summary-audited.json`: `2e7a921b8e525459489752ff97ee5033b5fde55d0dd3768692668b25442d3df2`
- `benchmarks/results/langgraph-support-v1/20260904-primary-audit/audit.json`: `83b9eaffdb717c0fcfe48113f58fcc5a772c6e9d39c3eba945033f3955e5f065`
- `benchmarks/results/langgraph-support-v1/20260904-performance/summary.json`: `9fc1b96f10b268c1c15be54df8bb1d6d0cc010ea1dc46c8aaa3fa8201d752bca`
- `benchmarks/results/langgraph-support-v1/20260904-stability/stability-analysis.json`: `c1ac5e69cbd562680b2ab41760d7b2ac4ae97b3a9672b621a6ecf3791717624a`
- `benchmarks/results/langgraph-support-v1/20260904-primary-audit/axiom-integration.json`: `0e31678ad7d3918a2f71ebcee4cb4fe5f37f2c2b63093a1b82b8d58b339b9e85`
- `benchmarks/results/langgraph-support-v1/20260904-stability/excluded-infrastructure-attempts.jsonl`: `e4843f0780359a3e84a532b52ec066cb0624dec1951a221cb7f5a2a382c302f7`

## Raw result paths

- Smoke audit: `benchmarks/results/langgraph-support-v1/smoke-audit-final/smoke-audit.json`
- Primary raw execution: `benchmarks/results/langgraph-support-v1/20260904-primary`
- Primary audit: `benchmarks/results/langgraph-support-v1/20260904-primary-audit`
- Performance: `benchmarks/results/langgraph-support-v1/20260904-performance`
- Stability: `benchmarks/results/langgraph-support-v1/20260904-stability`
- Axiom integration: `benchmarks/results/langgraph-support-v1/20260904-primary-audit/axiom-integration.json`
