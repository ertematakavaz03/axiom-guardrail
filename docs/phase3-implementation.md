# Phase 3 architecture and implementation ledger

Baseline: `593142ed8f1d5808b49db13e52739831d92368a3` on
`phase3/security-redteam-mcp`. Main is preserved. Annotated tag `v0.2.0`
resolves to `a5f73a8a506c54cc509d1208859f710a9c08331b` and is preserved.
External Benchmark #1 inputs and evidence are immutable.

## Architecture audit

The product uses FastAPI, SQLAlchemy/PostgreSQL, ARQ/Redis, an orchestration
graph, framework-neutral agent SDK contracts, deterministic evaluators, and
Next.js. Phase 2 adds Qdrant retrieval. Runs already snapshot agent versions,
suite policy and scenarios; cases persist trace events and evaluator evidence.
These resources are reused for security suites. Security is an explicit
scenario contract in scenario metadata, not an unrelated run application.

The legacy gateway executes sandbox calls after adapter execution. It cannot
prove prevention of an external agent action. Phase 3 therefore adds an
execution-path gateway that calls a supplied executor only after policy checks,
and records local enforcement receipts. External adapter observations can never
mint those receipts. Existing Phase 1/2 behavior remains compatible.

Security policy is versioned and scoped to project and optionally agent. Runs
snapshot policy and MCP approved inventories. MCP inventory hashes include
identity, transport, capabilities, tools, descriptions, schemas, resources and
prompts. A changed inventory requires renewed approval. Client-side inventory
pinning detects visible changes, not invisible changes in server implementation.

Confirmation grants bind trusted user/tenant/project/run context, an exact
action digest and expiry. Grants are single use; agent text and tool arguments
cannot issue them. Policies check scopes and destinations before execution.

Raw security observations are preserved separately from derived findings with
content hashes and event references. Corrections create new evaluations; they
must not modify observations. Only synthetic canaries are used in the shipped
corpus. Raw evidence access follows existing project membership controls.

## Implementation sequence

1. Contracts, reason catalog, severity and outcomes; strict validation tests.
2. Data-driven policy, confirmation grants and execution gateway; bypass tests.
3. MCP registry, protocol fixtures, drift/poisoning checks and MCP enforcement.
4. Deterministic corpus, reproducible mutations, vulnerable demo and evaluator.
5. Existing run worker, policy/registry API, persistence and recovery integration.
6. Existing run/case UI security views; evidence metrics and reports.
7. Execute and audit benchmark, measure overhead, full regression, Docker and
   browser verification, privacy/artifact review, commit and branch-only push.

## Acceptance rules

BLOCK is a product gate, not an attack outcome. ATTACK_SUCCEEDED requires an
unsafe consequence; ATTACK_BLOCKED requires a local enforcement receipt and no
successful consequence. Missing/incomplete evidence requires manual review.
Phrase matching can flag suspicious content but cannot establish injection
success. No model API is required. Unknown token/cost values remain unknown.

MCP local fixtures use JSON-RPC 2.0 and the explicitly negotiated 2025-06-18
protocol subset (initialize, initialized, tools/list, tools/call, resources/list,
resources/read, prompts/list, prompts/get). Reference:
https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle
and https://modelcontextprotocol.io/specification/2025-06-18/server/tools .
No arbitrary network probing or credentials are part of the demo.

Status: implementation and verification complete. The measured results, known
limitations, regression gates and six representative browser checks are recorded
in [the completion checkpoint](phase3-continuation-checkpoint.md), with the
[requirements ledger](phase3-requirements-checklist.md) and canonical audit.
Branch delivery is verified after the completion commit; no Phase 4 work began.
