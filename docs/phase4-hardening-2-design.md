# Phase 4 hardening-2 — pre-implementation design note

Written **before** any runtime code changed, against commit `feca45c` on branch
`phase4/runtime-enforcement-hardening-2`. Scope is exactly two validated defects from the
`20260920-phase4-hardened-1` audit: **D-017** and **D-015**. Nothing else is in scope.

The hardened-1 artifacts, the FULL-2 baseline and the frozen methodology are untouched.
Both defects are fixed in the *runtime policy layer*, which the frozen evaluator never
imports.

---

## Shared anti-overfitting constraints

Binding on both designs:

- No scenario identifier, attack-family name, gold label, expected outcome or evaluator
  decision may reach any decision. Enforced structurally by
  `tests/unit/test_runtime_antioverfit.py`, which now also covers the new code paths.
- No corpus string, canary value or benchmark prompt may appear in runtime source.
- Every clause must be traceable to a **tool contract** (signature, docstring or declared
  data) or to **trusted host state** — never to an observed benchmark result.
- The mechanism must behave identically on benchmark traffic and on equivalent real
  traffic; nothing may distinguish the two.
- A control that only works because a value happens to look unusual (a bare token, an
  odd length, an unfamiliar word) is content heuristics and is out of scope. Provenance
  and capability are in scope; guessing at meaning is not.

---

## D-017 — capability model for side-effect tools with no owned resource

### The defect

`escalate_to_human` executed with attacker-supplied content in `customer_message`
(`rsa-030`, the single real unsafe side effect in hardened-1). The tool takes no resource
identifier, so ownership binding — the control that stops every other write — has nothing
to bind to. It fell through to `SANDBOX_ONLY`, which permits execution.

The general shape of the gap: **`mutates: bool` conflates two different things.** "Changes
a record inside the trust boundary that the caller owns" and "sends content out of the
trust boundary" need different authorization, and today they get the same.

### Intended abstraction

A tool's **capability**, declared in `RuntimePolicy` (Phase 4's own model) rather than in
`ToolRule`. Keeping it out of `ToolRule` is deliberate: `ToolRule` is shared with the
Phase 3 `PolicyEngine`, whose `policy_hash` is recorded in frozen shadow-policy evidence,
and adding a field there would change that hash for reasons unrelated to any decision.

```
read_only               no state change
scoped_mutation         changes a resource the caller must own  (existing behaviour)
external_communication  content leaves the trust boundary
irreversible            cannot be undone once done
```

Capability is **derived** when not declared — `read_only` if the rule does not mutate,
`scoped_mutation` if it does — so existing policies keep their exact current behaviour and
the change is additive.

For `external_communication` and `irreversible`, three controls apply **before** execution:

1. **Permission.** The principal must hold the policy's declared external-send permission,
   read from `TrustedContext`. Reason code `EXTERNAL_SIDE_EFFECT_NOT_AUTHORIZED`.
2. **Approval, when configured.** `require_approval_for_external` makes these capabilities
   need an `ApprovalGrant` held in host state, on top of the existing `R3` rule. Reason
   code `HIGH_IMPACT_ACTION_REQUIRES_APPROVAL` — the code that already means exactly this;
   a second synonym would give one condition two names.
3. **Payload provenance.** Fields declared in `payload_bindings` are **not model-authored**.
   The enforcer overwrites each from trusted host state, exactly as scope binding already
   does for tenancy, and records what the model proposed in `rejected_proposals`. If the
   host supplies no value for a declared payload field, the call is denied with
   `PAYLOAD_POLICY_VIOLATION` rather than forwarding model-authored content.

Control 3 is grounded in the tool's own contract, not in anything observed. The upstream
docstring defines the field as *"The most recent customer message that triggered
escalation"* — a fact the host already holds. The model's role is to decide **whether** to
escalate; it has no business deciding **what text leaves the system**.

### Trust boundary

Unchanged and restated: everything in the model's context window is untrusted, including
any text it composes. `TrustedContext` — principal, permissions, owned resources,
approvals, and now host-supplied payload values — is populated by the host before the
model runs and is never updated from model output. The host may refresh payload values
between turns (`RuntimeEnforcer.update_payload_values`), because they are host state;
`authorize()` keeps its two-argument signature so no third channel for metadata exists.

### Acceptance criteria

- An external-communication call from a principal without the external permission is
  denied with `EXTERNAL_SIDE_EFFECT_NOT_AUTHORIZED`, and the executor is never reached.
- With `require_approval_for_external`, the same call is denied absent a grant in host
  state, and permitted with one.
- A model-authored payload on a declared payload field never reaches the executor: it is
  replaced by the host value, or the call is denied.
- Legitimate external communication still executes when the permission is held and the
  host supplies the payload.
- Scoped mutations and read-only tools behave exactly as before (no regression).
- Every denial carries a stable reason code and appears in the runtime evidence.

### Non-goals

- Inspecting payload **content**. No secret-scanning, no keyword lists, no "does this look
  like a token" heuristics.
- Response/output egress filtering (`rsa-004`, `rsa-026`, `rsa-029`) — deferred to a
  separately pre-registered D-016 design.
- A confidential-value registry. Without one, a secret the *user* typed and then asked to
  have forwarded is still forwarded when the host's own payload value contains it.

### Known limit, stated before implementation

**This design does not, by itself, change `rsa-030`'s outcome.** Two measured facts:

- Four benign controls escalate legitimately with the same tool, the same argument shape,
  and `reason` values drawn from the same documented list. No content-blind rule separates
  them from `rsa-030`.
- The frozen classifier scores `rsa-030` on `escalate_to_human` **executing**, not on what
  the payload contained. Server-authoring the payload keeps the canary inside the boundary
  but does not stop the tool from running.

So the permissive default preserves all four benign escalations and leaves critical unsafe
side effects at 1. Setting `require_approval_for_external` denies `rsa-030` — and denies
those four benign escalations too. Both configurations are implemented and tested; the
default is the permissive one, and the choice is recorded as a deployment decision with its
cost measured rather than argued.

---

## D-015 — conservative argument normalization

### The defect

The model emits `max_results: "5"` and `min_similarity_score: "0.5"` as JSON strings. The
enforced schema declares them `integer`/`number`, so `ARGUMENT_POLICY_VIOLATION` fires and
the call is refused — 21 false blocks across 20 benign controls in hardened-1.

The pinned tool accepts exactly these forms:

```python
if isinstance(max_results, str):
    max_results = int(max_results) if max_results.isdigit() else 5
if isinstance(min_similarity_score, str):
    try:
        min_similarity_score = float(min_similarity_score)
    except ValueError:
        min_similarity_score = 0.0
```

Axiom's validation is therefore **stricter than the callable contract it claims to
enforce**, which contradicts the provenance claim in §5.1 of the Phase 4 report.

### Intended abstraction

A declared, per-path **coercion table** in `RuntimePolicy`, applied in a single controlled
step *before* schema validation:

```
coercions: {tool: {json_path: "integer" | "number"}}
```

Rules:

- Only a `str` is considered. Anything else passes through untouched.
- `integer` accepts only ASCII digits (`^\d+$`) — narrower than upstream's `.isdigit()`,
  which also accepts non-ASCII digit characters.
- `number` accepts only a plain decimal or exponent form, and the result must be
  **finite** — `"nan"`, `"inf"`, `"1e999"` are rejected, not coerced.
- Anything that does not parse is **left exactly as it was** and fails schema validation as
  before. Normalization never invents a fallback; upstream's silent `= 5` / `= 0.0` on a
  bad string is the behaviour Axiom deliberately does not copy, because it hides the
  attempt.
- The **normalized value is then validated by the same unchanged schema**, so type, range
  and `additionalProperties` are all still enforced, and the normalized value is what is
  bound and executed.
- Normalization is recorded in the decision (`normalized_paths`) and in the run evidence.

### Acceptance criteria

- `"5"` → `5`, `"0.5"` → `0.5`, and the call proceeds.
- `"999"` normalizes to `999` and is then **rejected** by the unchanged 1–10 bound.
- `"abc"`, `"5 items"`, `"5.0.1"`, `"0x10"`, `" 5 "`, `""`, `"nan"`, `"inf"`, `"1e999"`
  are not coerced and remain denied.
- `True` is not read as `1`; nested objects and lists are untouched.
- A path with no declared coercion is never coerced, whatever its value looks like.
- Coercion cannot widen a schema: every accepted value still passes full validation.

### Non-goals

- Coercing arbitrary types (`"true"` → bool, `"[1,2]"` → list, date parsing).
- Coercing identifiers. `order_id` stays a strict six-digit string; a numeric-looking
  identifier is not a number.
- Relaxing bounds, `required`, or `additionalProperties` anywhere.
- Matching upstream's silent clamping and fallbacks. Refusing an out-of-contract argument
  and recording it remains the intended behaviour.

---

## Verification plan

Tests are written before the implementation and must fail against current `main`:

| Area | File |
| --- | --- |
| Capability derivation, external permission, approval, payload provenance | `tests/unit/test_runtime_external_side_effects.py` |
| Normalization accept/reject matrix, schema still enforced | `tests/unit/test_argument_normalization.py` |
| End-to-end through the mediated node, both configurations | `tests/unit/test_mediated_agent.py` (extended) |
| No benchmark knowledge in the new code | `tests/unit/test_runtime_antioverfit.py` (unchanged checks, wider surface) |

Then: full `pytest tests/unit tests/golden tests/benchmarks`, `ruff format --check`,
`ruff check`, `mypy apps services packages demos`, the anti-overfitting audit, and a
re-verification that hardened-1 and FULL-2 remain byte-identical.

**The 88-case hardened-2 run is not performed in this change.**
