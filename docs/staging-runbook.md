# Staging runbook

What it takes to run Axiom Guardrail somewhere other than a laptop, and — just as
important — what is **documented but not yet built**. Nothing below claims a deployment
that does not exist. No paid infrastructure has been provisioned, and nothing here is
provider-specific.

---

## 1. What exists today

| Capability | State | Where |
| --- | --- | --- |
| API | FastAPI, stateless per request | `apps/api` |
| Worker | ARQ over Redis | `apps/api/app/workers` |
| Database | PostgreSQL via SQLAlchemy, Alembic migrations | `alembic/` |
| Cache / queue | Redis | `AGENTARENA_REDIS_URL` |
| Vector store | Qdrant, per-tenant collection prefix | `AGENTARENA_QDRANT_*` |
| Liveness probe | `GET /livez` — no external checks | `apps/api/app/main.py` |
| Readiness probe | `GET /readyz` — Postgres, Redis, Qdrant | same |
| Structured logs | JSON, secrets redacted at the formatter | `apps/api/app/logging.py` |
| Correlation ids | `request_id`, `organization_id`, `project_id`, `run_id`, `case_id` | same |
| Local orchestration | Docker Compose | `docker-compose.yml` |
| CI | tiered: PR, nightly, release gate | `.github/workflows/` |

## 2. What is not built

Listed plainly so a reader does not infer a maturity that is not there.

- No infrastructure-as-code, no managed-service provisioning, no container registry
  publishing pipeline.
- No secret-manager integration. Secrets are read from the environment; `.env.example`
  documents names and carries no values.
- No TLS configuration — termination is assumed at an edge proxy or load balancer.
- No autoscaling policy, no per-tenant rate limiting, no LLM budget enforcement.
- No backup, restore or retention automation.
- No blue/green or canary deployment machinery.

---

## 3. Environment separation

Three environments, distinguished only by configuration:

| | development | staging | production |
| --- | --- | --- | --- |
| Data | disposable | synthetic, resettable | real |
| Enforcement mode | `shadow` while tuning | `enforce` | `enforce` |
| Tool side effects | sandbox | sandbox | real |
| Secrets | `.env` file | secret manager reference | secret manager reference |
| Migrations | manual | automatic on deploy | reviewed, then on deploy |
| Benchmarks | ad hoc | full suite on a schedule | never |

`AXIOM_ENFORCEMENT_MODE` must be set explicitly. There is deliberately no permissive
default; an unrecognised value raises at graph construction rather than quietly falling
back, because "which mode was that run in?" is the first question anyone asks of a
security artifact.

---

## 4. Required configuration

All read from the environment. Names only — never commit values.

```
AGENTARENA_DATABASE_URL          postgresql+asyncpg://…
AGENTARENA_REDIS_URL             redis://…
AGENTARENA_QDRANT_URL            http://…
AGENTARENA_QDRANT_COLLECTION_PREFIX
AGENTARENA_JWT_SECRET            secret-manager reference
AXIOM_ENFORCEMENT_MODE           enforce | shadow
AXIOM_UPSTREAM_SRC               benchmark targets only; a private host path, never committed
```

In staging and production these are **references** resolved by the platform at start-up,
not literals in a file or an image layer. An image that contains a credential has
distributed that credential to everyone who can pull it.

---

## 5. Deploy sequence

1. **Build** the image from `Dockerfile`; tag with the commit SHA, never `latest` alone.
2. **Migrate**: `alembic upgrade head` as a one-shot job, before any new instance serves.
   Migrations must be backward-compatible with the running version, so a rollback does not
   need a down-migration under load.
3. **Roll** the API, waiting for `/readyz` before adding an instance to rotation.
4. **Roll** the workers. They are idempotent per job; draining before replacing avoids
   duplicated side effects.
5. **Smoke**: `/livez`, `/readyz`, one authenticated read, one enqueued run.

### Probes

| Probe | Path | Failure means |
| --- | --- | --- |
| Liveness | `/livez` | The process is wedged — restart it |
| Readiness | `/readyz` | A dependency is unavailable — take it out of rotation, **do not** restart |

Pointing liveness at a dependency check is the classic way to turn a brief database blip
into a rolling outage. The two endpoints exist precisely so that cannot happen.

---

## 6. Rollback

1. Re-deploy the previous image tag.
2. Leave the database forward-migrated. Only roll a migration back if it is genuinely
   incompatible, and only with the service stopped.
3. Redis holds queue state; draining before rollback avoids re-running a job whose side
   effects already landed.
4. Qdrant collections are additive per prefix; a rollback does not require a vector
   rebuild.

## 7. Data retention and deletion

Not implemented; documented so it is not forgotten.

- Run artifacts and traces contain customer-supplied prompts, and are subject to whatever
  retention the deployment commits to.
- Deletion must cascade: Postgres rows, Qdrant points under the tenant's prefix, and any
  artifact storage.
- Benchmark evidence under `benchmarks/results/` is **immutable**. It is never
  regenerated, and a retention policy must exclude it or the project loses its baselines.

## 8. Security defaults review

| Default | Value | Why |
| --- | --- | --- |
| Enforcement mode | must be set explicitly | ambiguity in a security artifact is a defect |
| Mutations | `allow_mutations` defaults to **false** in `SecurityPolicy` | a write is opt-in |
| Unknown tool | denied (`TOOL_NOT_REGISTERED`) | fail closed |
| Unlabelled context row | dropped (`context_for_model`) | absence of a tenant label is not evidence of sharing |
| Schema | `additionalProperties: false` on every mediated tool | an undeclared argument cannot be smuggled in |
| Writes in a sandbox environment | `SANDBOX_ONLY` | development cannot touch real state |
| Retries | bounded (`max_retries`) | |
| Tool steps | bounded (`max_tool_calls`) | |
| Model turn wall-clock | bounded (`upstream-turn-wall-clock-timeout-1`) | a hung turn is a runtime failure, never a defence |
| Secrets in logs | redacted at the formatter | redaction at the call site gets forgotten |
| Secrets to the browser | never; the API is the only holder | |

### Dev-only modes

- `shadow` enforcement records decisions and lets calls through. It is for rollout
  observation. **It is not a security control**, and a shadow run must never be reported
  as a blocked one — which is why `RuntimeDecision` carries `decision` and
  `effective_decision` separately.
- Compose ships development credentials for local Postgres/Redis/Qdrant. They are
  development values in a development file and must not be reused anywhere reachable.

---

## 9. Benchmark hosts are not staging

The real-agent security benchmark needs a local Ollama and a pinned upstream checkout, and
takes roughly 50 minutes of model time. It runs on an operator's machine, against a
deliberately chosen artifact directory, and its results are committed as evidence. It is
not part of any deploy, and it is not in CI.
