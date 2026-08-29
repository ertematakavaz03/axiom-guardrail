# Phase 1 architecture decisions

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

