# Phase 1–2 architecture decisions

## ADR-001: Complete immutable run snapshots

A run stores the selected agent/version fields, hashed system prompt identity, suite policy, full scenario payloads, evaluator versions, budgets, and creation timestamp. Workers execute only the snapshot. Scenario and agent edits after queueing therefore cannot change an in-flight result. Secrets and the raw system prompt are deliberately excluded.

## ADR-002: Stateless API and Redis-backed execution

FastAPI performs validation and transactional metadata writes only. arq owns long-running work. Case concurrency uses a fixed number of queue consumers rather than unbounded `gather`, and progress commits after each terminal case.

## ADR-003: Central sandbox tool gateway

Adapters describe requested calls but cannot execute registered side effects directly. The gateway applies allow-list, scenario forbid-list, call-count, schema, confirmation, and R2 sandbox rules in that order and emits a decision for every attempt. A denied call is evidence, not a silent omission.

## ADR-004: Deterministic-first evidence

Phase 1 evaluates facts that code can prove: tools, arguments, authorization, configured string/output rules, latency, tokens, and configured pricing. The semantic-judge protocol is an extension point only; no external judge or paid key is required.

## ADR-005: Organization scope in every lookup

The JWT identifies a user, never an organization supplied by the browser. Repository lookups join the resource's project to organization membership. This prevents enumeration and cross-tenant reads or writes.

## ADR-006: Failure-isolated observability

PostgreSQL traces are the source of truth. Langfuse is optional and best-effort; missing keys, an absent SDK, or export failure cannot change execution state or verdicts.

## ADR-007: PostgreSQL source of truth, Qdrant derived index

Corpus, document, immutable version, chunk text/metadata, retrieval configuration, and gold evidence live in PostgreSQL. Qdrant stores only derived dense/sparse search vectors and a payload reference to the PostgreSQL chunk. A collection is isolated per project, and every query additionally requires organization, project, and corpus filters. This defense in depth prevents a caller-controlled filter from weakening tenant scope.

## ADR-008: Deterministic local RAG baseline

Local development and CI use stable feature-hash embeddings, a stable sparse vocabulary, Reciprocal Rank Fusion, and token-overlap reranking. The embedding provider is replaceable through configuration, but evaluation correctness never requires a paid model. Fixed candidate bounds, upload limits, and timeouts keep ingestion and retrieval predictable.

## ADR-009: Immutable RAG run evidence

A RAG run snapshots the corpus version, retrieval config, embedding identity, retrieval parameters, reranker, collection name, tenant scope, and each scenario's gold evidence. Workers read the snapshot rather than mutable UI state. Traces persist the query, dense/sparse candidates, fused/reranked evidence, extracted claims, emitted citations, and evaluator decisions.

## ADR-010: Causal RAG verdict precedence

Evaluators may emit several findings for one case. The stored list retains all of them, but primary-reason ordering favors tenant/security breaches, retrieval timeout/failure, stale or missing gold evidence, unsupported claims, wrong citations, missing citations, and finally non-critical semantic warnings. `RAG_TENANT_SCOPE_VIOLATION` is a hard blocker independent of weighted score.

## ADR-011: Metric applicability is explicit

Unavailable metrics are stored as `null`, never coerced to zero. Recall and MRR require gold evidence; nDCG is only meaningful with graded relevance; citation precision/recall and groundedness require applicable claim/citation evidence. Run aggregation averages only actual values and reports `N/A` in the UI otherwise.
