# Axiom Guardrail

Evidence-first evaluation, red-team, and security testing for tool-using AI agents.

Axiom Guardrail evaluates agent behavior beyond final-text quality: tool selection
and arguments, RAG grounding and citations, authorization, security findings and
behavioral regressions. Versioned scenarios, persisted traces and reproducible
benchmarks support evidence-backed `PASS`, `WARN` and `BLOCK` release gates.

## What it does

- Connects agent workflows through a framework-neutral SDK and Generic HTTP adapter.
- Executes versioned scenario suites through asynchronous workers and immutable run snapshots.
- Validates tool calls, arguments, authorization, confirmation and execution budgets.
- Evaluates retrieval, citations and groundedness with deterministic checks.
- Runs controlled red-team scenarios and MCP/tool-policy checks.
- Preserves event-level evidence, findings and benchmark exports for audit and comparison.
- Exposes projects, suites, runs, cases and traces through a Next.js interface.

## Why it exists

Unit tests alone leave gaps in nondeterministic agent behavior. A plausible answer
can conceal the wrong tool, invalid arguments, an unauthorized action, prompt
injection, cross-tenant access, unsupported claims or broken citations. Axiom
makes those behaviors inspectable and records evidence for regression decisions.

## Current project status

**Phase 3 — Security / Red-Team / MCP is complete.** Phase 4 has not started.

| Phase | Scope | Status |
|---|---|---|
| 1 | Core execution and evaluation | Complete |
| 2 | RAG evaluation | Complete |
| 3 | Security / Red-Team / MCP | Complete |
| 4 | Runtime enforcement, CI hardening and cloud/staging readiness | Next |
| 5 | Fine-tuning and optimization experiments | Planned |

## Verified Phase 3 results

The canonical Security Lab contains 65 attacks across 13 categories and 13 benign
controls, executed once in observational mode and once in preventive mode.

| Measurement | Verified result |
|---|---|
| Backend regression | 192 passed, 0 skipped; 48.96 seconds |
| Canonical security executions | 156 |
| Persisted security findings | 261 |
| Audit reconciliation | Hashes and recomputed metrics match; 0 unresolved cases |
| Preventive attack outcomes | 10 succeeded / 55 blocked (65 attacks) |
| Detection | 65/65 attacks in each mode (100%) |
| Benign false positives | 0/13 controls in each mode |

Observational mode recorded 62 successful attacks and three raw review outcomes.
Detection is not prevention: ten preventive response/log disclosures remain real
failures. These measurements describe this controlled corpus, not general security
or false-positive guarantees. PostgreSQL snapshots, evaluations, raw events and
findings matched the exports. Migration roundtrip, Ruff, mypy, frontend gates and
all six representative browser checks passed at the completion checkpoint.

## Architecture

| Component | Responsibility |
|---|---|
| Python / FastAPI | Scoped APIs, authentication, scenario and run management |
| PostgreSQL / SQLAlchemy / Alembic | Versioned resources, immutable snapshots, traces, findings and migrations |
| Redis / ARQ | Asynchronous evaluation jobs and bounded worker concurrency |
| LangGraph | Case orchestration, adapter execution and evaluator flow |
| Qdrant | Dense/sparse retrieval indexes with organization, project and corpus filters |
| Tool gateway / security services | Policy validation, trusted execution receipts and local MCP inventory checks |
| Next.js / TypeScript | Project, run, case and evidence views |
| Docker Compose / GitHub Actions | Local stack and automated quality/integration checks |
| Langfuse (optional) | Best-effort run/case/tool observability; export failures do not affect verdicts |

The API persists a run snapshot and queues work; the worker executes cases and
persists progress before aggregating results. PostgreSQL is the source of truth;
Qdrant holds derived retrieval indexes. See [architecture decisions](docs/architecture-decisions.md)
and [RAG evaluation](docs/rag-evaluation.md).

## Security model

Raw agent outcomes, detector findings, policy decisions, enforcement receipts and
externally visible effects are separate evidence. A `BLOCK` product verdict can
report an attack that already succeeded. `ATTACK_BLOCKED` requires trusted local
enforcement evidence and no successful consequence; incomplete evidence remains
`MANUAL_REVIEW_REQUIRED`.

The execution-path gateway checks tool/schema policy, trusted scope, destinations,
budgets and action-bound single-use confirmation before invoking its executor.
Tests cover prompt injection, unauthorized actions, disclosure, tenant boundaries,
MCP poisoning, capability/schema drift and tool-name shadowing. External HTTP
adapters provide observations; preventing their actions requires a host-owned
executor. Response/log/egress enforcement is a Phase 4 priority.

## Evaluation model

Deterministic checks come first: execution outcomes, tool selection, argument
schemas, authorization and resource limits. RAG evaluation adds Recall@k, MRR,
graded nDCG where applicable, citation support and groundedness. Claim extraction
and support currently use deterministic lexical checks; structured semantic
judges are future work.

Findings retain reasons and trace references. Quality runs aggregate scores with
hard-blocker overrides; security runs report attack outcomes separately and show
`N/A` for unrelated quality scores or unavailable telemetry.

## Repository structure

```text
apps/api/                FastAPI routes, persistence and ARQ worker
apps/web/                Next.js interface
services/orchestrator/   LangGraph case execution and agent adapters
services/evaluators/     Deterministic checks and score aggregation
services/tool_gateway/   Tool registry, policy and sandbox state
services/security/       Security contracts, gateway, MCP, evaluator and metrics
services/rag/            Ingestion, embeddings, retrieval and RAG evaluation
services/observability/  Optional Langfuse integration
packages/agent_sdk/      Framework-neutral execution contracts
demos/                   Support, RAG and Security Lab fixtures and tooling
benchmarks/              Benchmark definitions and preserved result artifacts
tests/                   Unit, golden, benchmark and integration tests
alembic/                 Database migrations
docs/                    Contracts, reports, audits and completion checkpoints
infra/docker/            API and web container definitions
```

## Quick start

Requirements: Docker with Compose v2 and available ports 3000, 8000, 5432, 6379
and 6333. The commands below use a POSIX shell; PowerShell can use
`Copy-Item .env.example .env` for the copy step.

```bash
git clone https://github.com/ertematakavaz03/axiom-guardrail.git
cd axiom-guardrail
cp .env.example .env
# Set a new AGENTARENA_JWT_SECRET in .env before starting.
docker compose up --build
```

Compose starts PostgreSQL, Redis, Qdrant, schema migrations, API, worker and web.
Open [the application](http://localhost:3000) or
[API documentation](http://localhost:8000/docs). Register a local account to
create projects. Optional support and RAG demo seeds:

```bash
docker compose exec api python -m demos.support_agent.seed
docker compose exec api python -m demos.rag_research.seed
```

The seed modules define local fixture accounts. In a project's **Security** page,
install the local lab and choose observational or preventive execution. Demo
side effects and canaries are synthetic. No model API key is required for the
deterministic demos. See [.env.example](.env.example) for configuration and the
[Generic HTTP contract](docs/generic-http-agent.md) for external agents.

## Testing

For local tooling, use Python 3.12+ and Node.js 24:

```bash
python -m pip install -e ".[dev]"
npm --prefix apps/web ci
pytest tests/unit tests/golden tests/benchmarks -q
ruff format --check .
ruff check .
mypy apps services packages demos
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

For the full isolated backend regression, start the Compose infrastructure and
create the dedicated test database once on a fresh local stack:

```bash
docker compose up -d postgres redis qdrant
docker compose exec postgres createdb -U agentarena agentarena_phase3_test
python -m demos.security_lab.verify --docker --full --migration-roundtrip
```

The database command assumes the default Compose user; adapt it if configured
differently, and skip creation if the database already exists. The verifier
applies migrations and isolates Redis/Qdrant namespaces. Integration tests
truncate the dedicated test database; use no valuable data there. They require
a database name ending in `_test`.

The verified Phase 3 baseline is **192 passed, 0 skipped**, with two ARQ
deprecation warnings. This is a historical baseline, not a fixed future test count.
GitHub Actions runs backend static/unit/benchmark checks, frontend checks/build
and PostgreSQL/Redis/Qdrant integration tests.

## Benchmark and security evidence

- [Phase 3 measured security report](docs/phase3-security-report.md)
- [Every-case security audit](docs/phase3-security-audit.md)
- [Phase 3 completion checkpoint](docs/phase3-continuation-checkpoint.md)
- [Canonical Security Lab exports and byte hashes](benchmarks/results/security-lab-v1/20260906-verified/)
- [External LangGraph support benchmark report](docs/benchmarks/langgraph-support-v1-report.md)

Verify the security evidence offline without re-executing the target:

```bash
python -m demos.security_lab.audit --source benchmarks/results/security-lab-v1/20260906-verified --output docs/phase3-security-audit.md
```

The earlier Security Lab startup-failure artifact contains zero executions and
is excluded from canonical results. External Benchmark #1 and Phase 3 raw evidence
remain preserved; audit interpretation does not overwrite observations.

## Known limitations

- Security coverage is primarily synthetic; benign coverage is narrow, including
  twelve repeated public-status controls and one MCP control per mode.
- Ten genuine preventive disclosure failures remain recorded. Allowed-read
  response/log sinks are outside the current execution gateway.
- Three observational raw-review outcomes remain intentionally retained even
  though fixture provenance resolved their audit questions.
- MCP inventory pinning detects visible inventory changes, not invisible changes
  in server implementation; remote MCP deployment and distributed consent storage
  are outside the current milestone.
- RAG citation support is lexical; local embeddings and reranking prioritize
  reproducibility. PDF ingestion is limited to text-based PDFs.
- Managed secret references, richer roles, worker cancellation and global
  cross-worker token reservations are not implemented.
- Cloud production deployment and complete security are not claimed. Benchmark
  results do not establish production capacity or universal attack prevention.

## Roadmap

**Next — Phase 4:** response enforcement, logging/telemetry protection, outbound
egress policy, expanded benign controls, CI release-gate hardening and
cloud/staging readiness. Start by defining acceptance criteria against the
preserved ten-disclosure ledger.

**Later:** broader agent adapters, model comparisons, and fine-tuned evaluator
or attack-generation experiments where evidence justifies them. No Phase 4 or
Phase 5 implementation is included in the completed Phase 3 milestone.

## Engineering principles

Evidence before scores. Deterministic checks before LLM judges. Immutable
benchmark observations. Security failures preserved rather than hidden.
Policy-bound execution and reproducible regression evidence.
