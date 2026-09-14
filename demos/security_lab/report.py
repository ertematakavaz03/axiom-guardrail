"""Render the Phase 3 report from persisted benchmark exports and their audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def percentage(value: dict[str, Any]) -> str:
    return (
        "N/A"
        if value["rate"] is None
        else f"{100 * value['rate']:.1f}% ({value['numerator']}/{value['denominator']})"
    )


def report(source: Path, destination: Path) -> None:
    manifest = json.loads((source / "artifacts-sha256.json").read_text(encoding="utf-8"))
    for name, expected in manifest.items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Evidence checksum mismatch: {name}")
    summary = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    audits = json.loads((source / "audit.json").read_text(encoding="utf-8"))
    cases = {
        mode: [
            json.loads(line)
            for line in (source / f"{mode}-cases.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        for mode in ("observational", "preventive")
    }
    runs = {run["mode"]: run for run in summary["runs"]}
    observed, prevented = [runs[mode]["metrics"] for mode in ("observational", "preventive")]
    lines = [
        "# Axiom Guardrail Phase 3 measured security report",
        "",
        "## Executive summary",
        "",
        f"Two real API → Redis queue → worker → PostgreSQL runs completed {summary['cases_per_run']} cases each: 65 attacks across 13 categories and 13 benign controls. The controlled target is deliberately vulnerable and uses no LLM.",
        "",
        f"Observed attack success: {percentage(observed['attack_success_rate'])}. With the execution gateway: {percentage(prevented['attack_success_rate'])}. Preventive-mode attack blocking: {percentage(prevented['attack_block_rate'])}. These rates use resolved attacks; inconclusive outcomes remain explicit.",
        "",
        f"The audit checked {summary['audit']['cases']} cases and classified every finding; unresolved audit cases: {summary['audit']['unresolved_cases']}. Raw observations and evaluator outcomes are preserved.",
        "",
        "## Scope and target",
        "",
        "The deterministic security-lab interpreter follows embedded synthetic commands, including commands carried in untrusted retrieved content. All effects remain in a per-case sandbox or a local MCP subprocess. This measures execution boundaries and evaluator behavior, not natural-language model jailbreak resistance. No external egress or paid model calls occurred.",
        "",
        "## Security policy",
        "",
        "Runs snapshot the project/agent policy, authenticated application context, approved MCP inventories and evaluator version. The demo target uses separately identified synthetic tenant/user context. Policy enforces server-qualified tools, R0–R3 tool risk, permissions, resource ownership, tenant/user/project bindings, exact destinations, schemas, cumulative calls and retries. Mutations are disabled by the lab policy. A consent-gated report release proves trusted first execution and denial of grant replay.",
        "",
        "No single security score is reported. Security runs leave the unrelated legacy quality score and unavailable cost/token telemetry null. PASS/WARN/BLOCK is a gate verdict, distinct from attack outcome.",
        "",
        "## Corpus and coverage",
        "",
        "The corpus remains 65 attacks (five per category) plus 13 controls. The only scenario-quality correction replaces a replay transaction masked by mutation denial with a consent-gated report release. It does not grant agent text authority or tune the corpus to improve a score.",
        "",
        "| Category | Observational outcomes | Preventive outcomes |",
        "|---|---|---|",
    ]
    for category, row in observed["category_breakdown"].items():
        lines.append(
            f"| {category.replace('_', ' ')} | {json.dumps(row['outcomes'], sort_keys=True)} | {json.dumps(prevented['category_breakdown'][category]['outcomes'], sort_keys=True)} |"
        )
    lines += [
        "",
        "Category success rates use resolved attacks; detection and prevention use all attacks in that category. Cross-tenant access attempts and actual leakage are separately measured below.",
        "",
        "| Category / mode | Success | Detection | Prevention |",
        "|---|---:|---:|---:|",
    ]
    for mode, rows in cases.items():
        for category, metrics in runs[mode]["metrics"]["category_breakdown"].items():
            attacks = [
                r["evaluation"]
                for r in rows
                if r["evaluation"]["is_attack"] and r["evaluation"]["category"] == category
            ]
            detected = sum(bool(e["findings"]) for e in attacks)
            blocked = sum(e["outcome"] == "ATTACK_BLOCKED" for e in attacks)
            lines.append(
                f"| {category.replace('_', ' ')} / {mode} | {percentage(metrics['success'])} | "
                f"{100 * detected / len(attacks):.1f}% ({detected}/{len(attacks)}) | "
                f"{100 * blocked / len(attacks):.1f}% ({blocked}/{len(attacks)}) |"
            )
    lines += [
        "",
        "## Attack outcomes and detected vs prevented",
        "",
        "| Measure | Observational | Preventive |",
        "|---|---:|---:|",
    ]
    for key in (
        "attack_success_rate",
        "attack_block_rate",
        "attack_detection_rate",
        "prevention_rate",
        "prompt_injection_success_rate",
        "indirect_injection_success_rate",
        "authorization_bypass_rate",
        "confirmation_bypass_rate",
        "synthetic_secret_leakage_rate",
        "cross_tenant_access_attempt_rate",
        "cross_tenant_leakage_rate",
        "cross_user_leakage_rate",
        "excessive_agency_rate",
        "mcp_policy_violation_rate",
        "mcp_attack_success_rate",
        "unsafe_failure_recovery_rate",
        "benign_control_false_positive_rate",
    ):
        lines.append(
            f"| {key.replace('_', ' ')} | {percentage(observed[key])} | {percentage(prevented[key])} |"
        )
    for title, key in [
        ("Gate verdicts", "verdicts"),
        ("Attack outcomes", "outcomes"),
        ("Finding severity", "severity_distribution"),
        ("Reason-code distribution", "reason_codes"),
    ]:
        lines += [
            "",
            f"### {title}",
            "",
            f"Observational: `{json.dumps(observed[key], sort_keys=True)}`",
            "",
            f"Preventive: `{json.dumps(prevented[key], sort_keys=True)}`",
        ]
    lines += [
        "",
        "PREVENTED requires a host-owned denial receipt before dispatch and absence of executor completion/failure for that denied action. Observational imports cannot mint receipts. A case can contain both a denied action and a separate successful disclosure; its outcome remains ATTACK_SUCCEEDED.",
        "",
        "## Prompt injection, indirect injection and prompt extraction",
        "",
        "Direct and indirect injection success requires a defined unsafe consequence. Attack strings and synthetic canaries in input alone do not count as leakage. The intentionally unsafe target can copy privileged prompt/secret values into a response or log after an allowed read. Those output paths are detected, not prevented by the tool gateway. Literal, URL, base64 and hex canary variants are inspected; this is not a universal encoding detector.",
        "",
        "## Tool abuse, authorization and confirmation",
        "",
        "Forbidden or unregistered tools, invalid arguments, resource substitutions and missing permissions are evidenced by requested actions plus policy rules and completion/denial events. Dedicated executor-backed tests cover cross-tenant mutations and concurrent grant reuse across gateways sharing the trusted store. Grants bind action/context/expiry and are consumed atomically before execution. The local store is not a distributed authorization service.",
        "",
        "## Exfiltration, secret handling and cross-tenant isolation",
        "",
        "Leakage metrics require a protected canary in a forbidden sink and an actual disclosure/dispatch. Access-attempt metrics are separate: a blocked tenant substitution contributes no leakage. API ownership is enforced independently from target tool authorization. Destinations and canaries are synthetic, with no real credentials or real tenant data in benchmark exports.",
        "",
        "## MCP security",
        "",
        "The local stdio fixture negotiates the 2025-06-18 JSON-RPC subset and exercises tools, resources and prompts. Evidence retains initialization, inventories, before/after hashes, tool/schema/description changes, request arguments/results and received authorization scope. Checks cover allowed calls, forbidden servers/tools/resources, schema violations, poisoned descriptions/output, post-approval mutation, synthetic export and objectively confusable names. Risk metadata is advisory; trusted policy remains authoritative. Inventory pinning detects visible capability changes, not invisible server implementation changes.",
        "",
        "Poisoned returned text is recorded as suspicious content with a matching text path; it cannot alone establish attack success. The gateway guards later operations. It does not claim to undo an MCP operation that already returned content.",
        "",
        "## Resource abuse and unsafe failure recovery",
        "",
        "Call budgets are cumulative within a case, including multiple turns. Retries are bounded per server/tool. Controlled fail/malformed/timeout fixtures deliberately fail; the evaluator conservatively retains MANUAL_REVIEW_REQUIRED where a protocol failure alone cannot prove side-effect absence. The audit resolves their fixture provenance and empty local effect ledger without rewriting those raw outcomes. These are expected controlled failures, not unexplained infrastructure incidents or claimed successful attacks.",
        "",
        "## False-positive audit and findings",
        "",
        "See [the complete case/finding audit](phase3-security-audit.md) for all 261 finding dispositions and the individual three review cases and ten preventive disclosures. Zero unresolved audit cases does not erase the three raw review outcomes. No evaluator false positives were found or corrected after measurement. ATTACK_FAILED is zero in both modes.",
        "",
        "The 13 controls are category-labelled: twelve repeat public-status behavior and one exercises MCP. Their 0/13 false-positive rates are narrow measurements, not evidence of broad benign workload coverage.",
        "",
        f"Observed benign false positives: {percentage(observed['benign_control_false_positive_rate'])}; preventive benign false positives: {percentage(prevented['benign_control_false_positive_rate'])}. Every PASS/control was checked alongside every WARN/BLOCK.",
        "",
        f"Finding dispositions: `{json.dumps(summary['audit']['finding_dispositions'], sort_keys=True)}`.",
        "",
        "The audit verifies raw hashes, event references, persisted finding counts, completion evidence and ordered local denials. It records one disposition per finding. The detailed audit is separate from immutable raw traces; no score-improving evaluator rewrite was applied after measurement.",
        "",
        "## Performance / Axiom overhead",
        "",
        "All values are milliseconds. Policy samples include execution checks and evaluator rechecks; evaluator time includes its rechecks. Gateway time includes policy plus receipt/grant bookkeeping. These overlap and must not be added. MCP guard timings exclude subprocess/JSON-RPC latency. Full case latency includes target/runtime work and is not Axiom-only overhead.",
        "",
        "| Mode / measurement | Samples | Mean | p50 | p95 |",
        "|---|---:|---:|---:|---:|",
    ]
    for mode, run in runs.items():
        for key in (
            "deterministic_policy_overhead",
            "policy_gateway_overhead",
            "mcp_inventory_guard_overhead",
            "deterministic_evaluator_overhead",
            "case_latency",
        ):
            d = run[key]
            values = [
                "N/A" if d[v] is None else f"{d[v]:.4f}" for v in ("mean_ms", "p50_ms", "p95_ms")
            ]
            lines.append(
                f"| {mode} / {key.replace('_', ' ')} | {d['samples']} | {' | '.join(values)} |"
            )
    lines += [
        "",
        "## Remaining preventive failures",
        "",
        "These ten ATTACK_SUCCEEDED cases are genuine remaining security failures: allowed reads completed, followed by disclosure through a response or log. Execution-only Tool Gateway enforcement does not guard these sinks. Observational detection is not prevention; preventive mode cannot automatically block every unsafe final response. No evaluator or demo change was made to erase these failures.",
        "",
        "| Scenario | Disclosure finding | Sink evidence |",
        "|---|---|---|",
    ]
    for row in sorted(cases["preventive"], key=lambda r: r["scenario"]["id"]):
        if row["evaluation"]["outcome"] != "ATTACK_SUCCEEDED":
            continue
        findings = [f for f in row["evaluation"]["findings"] if f["consequence"]]
        references = {ref for f in findings for ref in f["event_ids"]}
        sinks = [
            f"{e['kind']} `{e['id']}`"
            for e in row["events"]
            if e["id"] in references and e["kind"] in {"response", "log"}
        ]
        lines.append(
            f"| {row['scenario']['id']} — {row['scenario']['name']} | "
            f"{', '.join(sorted({f['reason_code'] for f in findings}))} | {', '.join(sinks)} |"
        )
    lines += [
        "",
        "## Recommended remediations and limitations",
        "",
        "Keep secrets and privileged instructions out of target-visible output paths; apply output/egress controls where those paths must be prevented. Use explicit tool/destination scopes, reapprove changed inventories, review suspicious MCP definitions/content, and preserve failed-operation uncertainty. For deployment beyond the local harness, integrate the host-owned gateway with the real executor and use a persistent atomic grant store. External adapters remain observational. The fixed synthetic corpus does not certify security, cover all attacks, or establish production model robustness.",
        "",
        "## Reproduction and evidence",
        "",
        f"Evidence directory: `{source.as_posix()}`. Summary and per-case JSONL come from completed PostgreSQL-backed API runs. `artifacts-sha256.json` protects exported bytes; `audit.json` contains all finding dispositions.",
        "",
        "```bash",
        "# Verify and render the canonical evidence without executing the target:",
        "python -m demos.security_lab.audit --source benchmarks/results/security-lab-v1/20260906-verified --output docs/phase3-security-audit.md",
        "python -m demos.security_lab.report --source benchmarks/results/security-lab-v1/20260906-verified --output docs/phase3-security-report.md",
        "docker compose up -d postgres redis qdrant",
        "docker compose build api",
        "python -m demos.security_lab.verify --docker --full --migration-roundtrip",
        "# Optional future measurement only: create a NEW separate *_test demo database.",
        "python -m demos.security_lab.stack up",
        "python -m demos.security_lab.benchmark --api-url http://localhost:8001/v1 --output benchmarks/results/security-lab-v1/NEW-RUN",
        "python -m demos.security_lab.report --source benchmarks/results/security-lab-v1/NEW-RUN --output docs/phase3-security-report.md",
        "```",
        "",
        "The verifier uses the Docker network to avoid a Windows PostgreSQL/Docker port-5432 collision. Regression uses `agentarena_phase3_test`, Redis DB 15 and a test Qdrant namespace. The live demo uses `agentarena_phase3_demo_test`, Redis DB 14 and its own namespace. Neither migrates nor truncates the persisted benchmark database. Output directories must be new; the exporter refuses to overwrite evidence.",
        "",
        f"Measured run IDs: observational `{runs['observational']['run_id']}`, preventive `{runs['preventive']['run_id']}`.",
        "",
        f"Audit coverage: {len(audits)} cases. Measurement window: {summary['started_at']} to {summary['finished_at']}.",
        "",
    ]
    destination.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report(args.source, args.output)


if __name__ == "__main__":
    main()
