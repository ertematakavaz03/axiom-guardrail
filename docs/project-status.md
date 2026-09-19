# Project status

Last updated: 2026-09-20. Every figure here traces to a committed artifact.

## Where the project stands

Axiom Guardrail has a working end-to-end platform and, as of the FULL-2 run, a
**validated red-team baseline** against a real model-driven agent. That is the milestone
that separates an evaluation demo from an evaluation tool: there is now a frozen corpus, a
frozen scoring methodology, a reproducible execution harness, and a measured number that a
future change can be compared against.

What is *not* done: the hardening comparison. The baseline says the tested reference agent
is highly vulnerable. It does not yet say that Axiom's guardrails reduce that. That is the
next milestone, and until it is run, no defence claim is made.

## Completed and validated

| Phase | Scope | Status |
|---|---|---|
| 1 | Core execution and evaluation | Complete |
| 2 | RAG evaluation | Complete |
| 3 | Security / red-team / MCP | Complete |
| 3.5 | Benchmark validity: conformance vs real-agent robustness, validated FULL-2 baseline | **Complete** |
| 4 | Runtime enforcement, hardening comparison, CI gating, cloud readiness | Next |
| 5 | Fine-tuning and optimization experiments | Planned |

Phase 3.5 specifically produced:

- A separation of **policy conformance** (`security-lab-v1`) from **real-agent robustness**
  (`security-real-agent-v1`), with a written taxonomy so the two are never merged.
- An **enforcement-independent trace detector**, asserted structurally from the module AST
  rather than by comment.
- A frozen 88-case corpus: 48 attacks across 10 families (40 authored + 8 tracked
  paraphrase variants) and 40 unique benign controls.
- **System-prompt marker provenance** resolved through the real binding chain
  (`agent.py` → import → `prompts.py`), hash-pinned at both ends, confound-audited against
  every other corpus the model can see.
- A **pre-registered asymmetric evidence policy** for extraction attacks: a marker hit
  proves disclosure, a marker miss proves nothing and may never be scored as a defence.
- An execution harness that survives a stalled upstream response: per-turn wall-clock
  timeout, durable per-case checkpointing, validated resume.
- A **defect ledger** (D-001…D-014) recording every methodology error found, including the
  ones that made results look worse.

## Current benchmark — `security-real-agent-v1` FULL-2

Target: pinned third-party LangGraph customer-support agent on local llama3.1 8B (Q4_K_M).
Full report: [security-real-agent-v1-baseline.md](security-real-agent-v1-baseline.md).

| | |
|---|---|
| Cases completed | 88 / 88 |
| Attacks / benign controls | 48 / 40 |
| Authored / variant cases | 80 / 8 |
| Scorable attacks | 43 (5 held for manual review) |
| Runtime failures | 0 |
| Elapsed | ~2906 s |

| Outcome | Count |
|---|---|
| `ATTACK_SUCCEEDED` | 26 |
| `ATTACK_INFLUENCED_NO_UNSAFE_ACTION` | 8 |
| `ATTACK_REJECTED_BY_AGENT` | 5 |
| `UNSAFE_ACTION_ATTEMPTED` | 4 |
| `MANUAL_REVIEW_REQUIRED` | 5 |
| `SAFE_BEHAVIOR` | 40 |

| Rate | Value |
|---|---|
| `attack_success_rate` | 0.6047 |
| `unsafe_action_attempt_rate` | 0.6977 |
| `agent_influence_rate` | 0.8837 |
| `attack_rejection_rate` | 0.1163 |
| Benign controls safe | 40 / 40 (100%) |
| Unique semantic parents scored | 35 of 43 |

**How to read this.** The reference agent is highly susceptible to natural-language attack,
and the benign controls show the measurement is not simply flagging everything. Both halves
matter: a suite that reports a high attack success rate while also failing benign traffic
is measuring noise. This one does not.

Pilot-3 (12 cases) reported `attack_success_rate` 0.60 against FULL-2's 0.6047 — the pilot
reproduced the headline signal, but its other rates were artifacts of a five-case
denominator. **FULL-2 is the only reportable baseline.**

## Current limitations

- One target, one model, one quantization, one corpus, one day. Nothing here generalizes to
  other agents or models.
- The model digest is a 12-character prefix match; model identity is not proven.
- Ollama drifted from the recorded baseline (0.33.2 → 0.34.2) and the run records the drift
  rather than hiding it.
- No prevention was measured and none is claimed: the target owns its own tool layer.
- Extraction figures are a **lower bound on verbatim disclosure**. Exact-marker matching
  cannot see a paraphrase, so five extraction-objective attacks are held for manual review
  rather than counted either way.
- No repeated-run variance analysis exists for this suite, so a single future comparison
  cannot yet distinguish a small improvement from run-to-run noise.
- Response, log and egress enforcement are not implemented; ten genuine preventive
  disclosure failures from Phase 3 remain recorded and open.
- Cloud production deployment is a target, not a claim. MLflow tracking is not implemented.

## Next steps

1. **Decide the comparison design before hardening.** Either establish baseline variance by
   repeating the same frozen suite, or pre-register an effect size large enough that a
   single comparison is meaningful. Choosing after seeing the hardened number is the failure
   mode this phase was built to prevent.
2. **Apply hardening / guardrails** to the agent-facing path.
3. **Re-run the identical frozen FULL-2 suite** — same corpus digest, same classifier, same
   markers, same scoring.
4. **Report before/after side by side**, including benign controls. A drop in attack success
   that also degrades benign behaviour is not a win.

## Near-term roadmap

| Horizon | Work |
|---|---|
| Next | Hardening comparison against the frozen FULL-2 baseline |
| Next | Response / log / egress enforcement against the preserved ten-disclosure ledger |
| Next | CI release-gate hardening: fail a pipeline on a regression against a stored baseline |
| Later | Baseline variance study; broader benign controls |
| Later | Additional agent adapters and model comparisons |
| Later | Cloud/staging deployment; MLflow experiment tracking |
| Later | Structured semantic judges for claim support, where evidence justifies them |
