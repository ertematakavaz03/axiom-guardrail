# External LangGraph Support Benchmark v1 plan

Status: implementation plan; no benchmark score has been produced yet.

## Provenance and protected baseline

- Axiom repository baseline: annotated tag `v0.2.0`, resolving to commit
  `a5f73a8a506c54cc509d1208859f710a9c08331b`.
- Axiom work branch: `benchmark/langgraph-support-v1`.
- Upstream repository: `https://github.com/aperritano/langgraph-customer-support-agent`.
- Pinned upstream commit: `64dea789d7b59ae6a57470091d3dbf4ba43fe7cb`.
- Upstream is checked out detached at that commit, outside the Axiom repository.
- Upstream declares an MIT license in `pyproject.toml`; the pinned tree does not
  contain a standalone `LICENSE` file. Reports must preserve that distinction.
- Source code at the pinned SHA is authoritative when it differs from the README.
- The external application must not be modified to improve its benchmark score.

## Verified execution environment

- Windows 11, x86-64.
- Python 3.12.10 in the upstream-local `.venv312` virtual environment.
- `pip install '.[dev]'` completed and `pip check` reported no broken requirements.
- The upstream dependency ranges are broad minimums. The resolved versions include
  LangGraph 1.2.11, langchain-core 1.6.1, langchain-ollama 1.1.0,
  sentence-transformers 6.0.1, and pytest 9.1.1. A complete freeze will be stored
  with benchmark provenance.
- Ollama 0.33.2 is local at `http://localhost:11434`.
- Model: `llama3.1:latest`; digest
  `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e`;
  GGUF; 8.0B parameters; Q4_K_M; 4,920,753,328 bytes.
- The one-time cold/model warm-up already completed in 89,168 ms. That duration is
  setup and is excluded from every benchmark latency and throughput statistic.

## Actual upstream architecture

The application is a LangGraph ReAct-style graph. Its state contains an append-only
`messages` sequence. An `agent` node invokes `ChatOllama` with a system prompt and
bound tools. Conditional routing sends assistant tool calls to a `ToolNode`, whose
results return to the agent; an assistant response without tool calls ends the graph.
The configured model is `llama3.1:latest`, temperature 0, context window 4,096,
maximum generation 512 tokens, and request timeout 60 seconds.

`create_graph().compile()` has no application checkpointer. Direct in-process graph
invocations therefore do not persist memory between calls. Thread persistence, if
present in the benchmark server, is supplied by the LangGraph API runtime and must be
verified live rather than inferred from the graph alone.

Retrieval uses `HuggingFaceEmbeddings` with
`sentence-transformers/all-MiniLM-L6-v2`, normalized CPU embeddings, and an
`InMemoryVectorStore`. It indexes `data/knowledge_base.json` plus hard-coded general
support snippets. The search tool returns formatted rank, similarity score, category,
type, and content. It does not expose durable native document IDs. Any benchmark ID
mapped from exact source content will be labelled benchmark-derived.

## Bound tool inventory and schemas

The pinned source binds eight tools. The similarly named
`search_knowledge_base(query, category="general")` function is not bound and is not
an available agent tool.

| Tool | Arguments | Observed effect | Risk classification |
| --- | --- | --- | --- |
| `list_available_functions` | none | Lists capabilities | Read-only |
| `send_greeting` | none | Returns the prescribed greeting | Read-only |
| `search_vector_knowledge_base` | `query: str`; `max_results: int = 5`; `min_similarity_score: float = 0.0`; `categories: str = ""` | Searches the in-memory KB | Read-only retrieval |
| `get_order_status` | `order_id: str` | Reads a synthetic order | Read-only |
| `list_orders` | `status_filter: str = "all"` | Reads synthetic orders | Read-only |
| `initiate_return` | `order_id: str`; `reason: str` | Returns a simulated RMA and fee decision | Simulated action; no verified external side effect |
| `check_product_availability` | `product_name: str` | Reads mock inventory | Read-only |
| `escalate_to_human` | `reason: str`; `customer_message: str` | Returns/logs a simulated ticket | Simulated action; writes a local log line but no durable ticket |

The benchmark adapter may normalize these observed calls and results, but it must not
route them into Axiom's own action tools or claim that Axiom prevented an action the
upstream agent already performed.

## Source-backed business data

All gold facts must trace to the pinned tree:

- `src/support_agent/tools.py`: in-memory mock orders, inventory, order/return/ticket
  behavior, and return-shipping fee logic.
- `data/knowledge_base.json`: return, shipping, warranty, payment, product, support,
  and FAQ content.
- `src/support_agent/vector_store.py`: additional hard-coded support information and
  the retrieval/indexing behavior.
- `tests/` and `evaluations/`: upstream controls and intended examples, subject to
  source validation. Tests are not automatically accepted as correct gold facts.

Order dates are generated relative to the current time. Gold expectations must use
stable identifiers/status/products/tracking values or compare against the actual tool
result from the same execution, not hard-code generated dates.

The return implementation verifies an order and generates an RMA identifier. Contrary
to a docstring, it does not remove or mutate the order. Defective/damaged/broken
reasons receive free return shipping; other reasons report a $7.99 deduction. RMA and
escalation IDs use Python `hash()`, so their numeric suffixes are process-dependent and
must be validated by format and same-run tool evidence, not as fixed gold values.

Inventory is mock data: laptop 15/in stock; headphones 3/low stock; mouse 0/out of
stock with a source-coded restock date; keyboard 25/in stock; monitor 8/in stock; and
webcam 1/low stock.

The knowledge base states, among other facts, a 30-day unused/original-packaging
return policy, refunds in 5-7 business days, standard shipping in 5-7 business days,
express in 2-3 business days for $15, overnight next-business-day shipping for $25,
the source-listed payment brands and PayPal, warranty and FAQ facts, and source-listed
products. Case generation may paraphrase questions but may not add business facts.

## API protocol and integration strategy

The benchmark will first verify the live LangGraph API contract. The pinned example
uses `POST /threads`, `POST /threads/{thread_id}/runs`, and thread history/state reads;
the installed CLI may also support a wait endpoint. Live behavior and installed API
documentation are authoritative for transport details.

The integration is deliberately thin:

1. Run the pinned graph under the local LangGraph API using its isolated Python 3.12
   environment and local Ollama.
2. Use a reusable benchmark-local LangGraph HTTP mapping layer for thread creation,
   message submission, completion, history, and normalized observed traces.
3. Keep customer-support correctness rules in the manifest/validator, never in the
   transport adapter.
4. Persist externally observed results into Axiom's normal project/run/case, trace,
   and evaluation records. Do not send already-executed upstream calls through
   Axiom's `ToolGateway` a second time.

The Axiom hierarchy is:

`External Benchmarks` -> `LangGraph Customer Support Agent` ->
`External LangGraph Support Benchmark v1`.

## Observability contract

Capture when the live API actually exposes it:

- thread ID and case/thread-group identity;
- ordered messages and roles;
- tool names, arguments, results, and errors;
- final assistant response;
- retrieval query and the returned rank/score/category/type/content text;
- total external request/case latency;
- token usage metadata;
- retries and transport errors.

Metrics unavailable from the live API remain `N/A`; they are never estimated. Likely
unavailable fields include internal queue wait, per-tool latency, retrieval execution
latency, time to first token when using a non-streaming endpoint, and structured
citations. Retrieval Recall@K/MRR/nDCG are calculated only where a returned source can
be truthfully mapped to source-backed gold relevance. Citation precision/recall are
`N/A` when no citation structure exists.

## Dataset and manifest

The target is 100 unique primary cases and the permitted honest floor is 80. Planned
coverage is 10 upstream controls; 15 KB/policy; 15 order status; 15 returns; 10
inventory; 10 escalation; 10 multi-turn; 5 unknown/insufficient-information; 5
no-tool; and 5 privacy/isolation cases. Cases have easy, medium, or hard labels.

All cases are validated before execution for unique IDs, known bound tools, exact
argument schemas, source-backed order/product/KB facts, valid canaries, valid thread
groups, and satisfiable expectations. Paraphrased prompts and combinations of facts
may create unique cases; the expected facts themselves must remain upstream-backed.

A 12-case real-agent smoke gate covers KB, order, return, inventory, escalation,
multi-turn, invalid entity, and isolation behavior. A full run starts only after the
smoke artifacts demonstrate complete transport and persistence without adapter loss.

## Correctness and hallucination methodology

Evaluation is deterministic and evidence-backed; no paid LLM judge is used.

- Tool selection compares normalized observed tool calls with required, allowed, and
  forbidden tool sets.
- Argument checks compare schema, identifiers, products, and required semantic values.
- Expected facts use normalized matching and narrowly defined aliases/paraphrases.
- Business-claim extraction covers order IDs, tracking IDs, statuses, inventory
  counts, policy durations/costs, and generated RMA/ticket claims.
- A claim is supported only by the case's upstream gold sources or observed same-case
  tool/retrieval result. Contradictions and fabricated entities/values are findings.
- Claims that the deterministic validator cannot decide receive
  `MANUAL_REVIEW_REQUIRED`, not a guessed label.

For every finding store the actual claim, expected evidence, actual evidence, pinned
source reference, and reason. Aggregate factual claim count, supported, contradicted,
unsupported, fabricated values/entities, hallucination case rate, unsupported claim
rate, and grounded case rate. Only audited real-agent failures count as validated
external failures.

## Privacy and memory-isolation methodology

Only upstream mock fixtures and explicit synthetic benchmark canaries are used. Each
independent case creates a new thread. Only turns within an explicitly declared
multi-turn case share a thread. A unique canary introduced in one thread must not
appear in another. Verified cross-case canary disclosure is `BLOCK` with raw trace
evidence. This measures observed benchmark isolation only and is not a privacy or
enterprise certification.

The external server receives an explicit minimal environment rather than inheriting
the host environment. It may receive OS/runtime variables, local-only Ollama URL,
explicit cache locations, encoding settings, offline model flags after preparation,
and telemetry-disabled settings. It must not inherit authorization headers, bearer
tokens, cookies, API keys, database passwords, LangSmith credentials, or Axiom
secrets. Stored request/response metadata is redacted for those secret forms while
synthetic benchmark canaries remain intact as evaluation evidence.

Model and embedding downloads are preparation-time network activity. After the
embedding artifact is cached, measured conversations run with Hugging Face and
transformers offline modes and LangSmith/tracing disabled. Network observations and
unexpected outbound attempts are recorded; no conversation data is intentionally
sent to a paid third-party API.

## Performance and stability methodology

The primary correctness run uses concurrency 1. Setup, installation, model download,
container startup, embedding initialization/download, and the completed 89,168 ms
cold/model warm-up are excluded.

For observable timings store queue wait, external-agent latency, tool latency,
retrieval latency, total case latency, time to first token, retry count, tool-call
count, token counts, errors, and timeouts. Unobservable values are `N/A`. Report mean,
median/p50, p90, p95, p99, min, and max from completed measured cases. The primary
case timeout is derived from the 12-case smoke latency distribution with a documented
safety multiplier and bounds.

Throughput is a separate fixed 20-case read-only subset, run at C1, C2, and C4. Report
cases/minute, success rate, p95 latency, timeout rate, and error rate as **LOCAL
BENCHMARK THROUGHPUT**. It is not a production-capacity claim. Simulated action cases
are excluded from concurrent throughput.

Stability is also separate: 20 representative cases, three executions each. Report
verdict, tool, argument, fact, and hallucination consistency plus latency variability.
Repetitions never inflate or alter the 100-case primary score.

The runner writes one append-safe case artifact immediately after completion and
supports `--resume`, `--case`, `--category`, `--difficulty`, `--limit`, `--output`,
`--stability`, and `--performance`.

## Verdicts, audit, and known limitations

Every `WARN` and `BLOCK` is audited as one of `REAL_AGENT_FAILURE`,
`BENCHMARK_EXPECTATION_BUG`, `ADAPTER_BUG`, `EVALUATOR_FALSE_POSITIVE`,
`INFRASTRUCTURE_FAILURE`, `MODEL_NONDETERMINISM`, or `UNRESOLVED`. Benchmark,
adapter, and evaluator defects are fixed and affected cases rerun; the external agent
is not altered.

Known limitations include mock/non-persistent business actions, process-dependent
synthetic identifiers, no upstream-native retrieval document IDs, possibly absent
per-node timing and streaming TTFT, no structured citations, local single-machine
performance, model nondeterminism despite temperature 0, relative order dates, and
dependency resolution newer than the upstream's publication date. Reports expose
these limits and use `N/A` wherever evidence is not available.

## Execution gates

1. Freeze environment and record upstream test results independently of Axiom.
2. Start the real pinned graph with the minimal environment; verify five manual flows.
3. Build and validate the source-backed manifest, mapping, adapter, validator, runner,
   result importer, tests, and dashboard presentation.
4. Pass the 12-case real smoke gate and derive the primary timeout.
5. Execute/resume the concurrency-1 primary run.
6. Execute the fixed C1/C2/C4 read-only throughput runs and 20-by-3 stability run.
7. Audit every non-pass outcome and regenerate immutable result/report artifacts.
8. Import results into Axiom's normal data model and verify case drill-down.
9. Run Axiom Python and web regressions, a secret scan, and reproducibility checks.
10. Commit and push only `benchmark/langgraph-support-v1`; never merge, move the tag,
    force-push, start Phase 3, or begin another external benchmark.
