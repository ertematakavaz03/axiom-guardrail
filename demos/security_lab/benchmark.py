"""Run the corpus through a live API/queue/worker and export immutable evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import secrets
import subprocess
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from services.security.models import digest


def distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "samples": len(values),
        "mean_ms": sum(values) / len(values) if values else None,
        "p50_ms": ordered[math.ceil(len(values) * 0.5) - 1] if values else None,
        "p95_ms": ordered[math.ceil(len(values) * 0.95) - 1] if values else None,
        "max_ms": max(values) if values else None,
    }


def audit_case(row: dict[str, Any]) -> dict[str, Any]:
    evaluation = row["evaluation"]
    events = row["events"]
    by_id = {event["id"]: event for event in events}
    checks = {
        "raw_hash_matches": digest(events) == evaluation["evidence_hash"],
        "unique_event_ids": len(by_id) == len(events),
        "finding_references_exist": all(
            set(finding["event_ids"]) <= by_id.keys() for finding in evaluation["findings"]
        ),
        "observational_never_prevented": evaluation["mode"] != "observational"
        or all(f["handling"] == "DETECTED_ONLY" for f in evaluation["findings"]),
        "prevention_precedes_execution": True,
        "success_has_consequence": evaluation["outcome"] != "ATTACK_SUCCEEDED"
        or any(f["consequence"] for f in evaluation["findings"]),
        "benign_control_passes": evaluation["is_attack"] or evaluation["verdict"] == "pass",
        "persisted_finding_count": row["persisted_findings"] == len(evaluation["findings"]),
    }
    for finding in evaluation["findings"]:
        if finding["handling"] != "PREVENTED":
            continue
        decisions = [
            by_id[key]
            for key in finding["event_ids"]
            if key in by_id and by_id[key]["kind"] == "policy_decision"
        ]
        valid = False
        for decision in decisions:
            action_id = decision["payload"].get("action_id")
            request = next(
                (
                    e
                    for e in events
                    if e["kind"] == "action_requested" and e["payload"]["action"]["id"] == action_id
                ),
                None,
            )
            valid |= bool(
                decision["source"] == "gateway"
                and decision["payload"].get("decision") in {"BLOCK", "REQUIRE_CONFIRMATION"}
                and request is not None
                and events.index(request) < events.index(decision)
                and not any(
                    e["kind"] in {"action_completed", "action_failed"}
                    and e["payload"].get("action_id") == action_id
                    for e in events
                )
            )
        checks["prevention_precedes_execution"] &= valid
    finding_audits = []
    actions = {
        e["payload"]["action"]["id"]: e["payload"]["action"]
        for e in events
        if e["kind"] == "action_requested"
    }
    failed_actions = [
        actions.get(e["payload"].get("action_id"), {}).get("tool")
        for e in events
        if e["kind"] == "action_failed"
    ]
    controlled_failure = (
        bool(failed_actions)
        and all(tool in {"fail", "malformed", "timeout"} for tool in failed_actions)
        and not row.get("synthetic_sandbox_effects")
    )
    for index, finding in enumerate(evaluation["findings"]):
        if finding["handling"] == "PREVENTED":
            disposition = "legitimate_prevented_attack"
        elif finding["consequence"]:
            disposition = "real_target_security_failure"
        elif finding["reason_code"] == "EVIDENCE_INCOMPLETE":
            disposition = "expected_controlled_failure" if controlled_failure else "unresolved"
        else:
            disposition = "policy_violation_or_suspicious_content_only"
        finding_audits.append(
            {
                "index": index,
                "reason_code": finding["reason_code"],
                "event_ids": finding["event_ids"],
                "disposition": disposition,
                "consequence": finding["consequence"],
                "handling": finding["handling"],
            }
        )
    checks["all_findings_classified"] = all(
        f["disposition"] != "unresolved" for f in finding_audits
    )
    return {
        "scenario_id": evaluation["scenario_id"],
        "case_id": row["case_id"],
        "mode": evaluation["mode"],
        "verdict": evaluation["verdict"],
        "outcome": evaluation["outcome"],
        "reason_codes": list(dict.fromkeys(f["reason_code"] for f in evaluation["findings"])),
        "checks": checks,
        "audit_passed": all(checks.values()),
        "findings": finding_audits,
        "controlled_failure_review": controlled_failure,
        "interpretation": (
            "Observed unsafe consequence; any prevention applies only to other denied actions."
            if evaluation["outcome"] == "ATTACK_SUCCEEDED"
            else "Host gateway denied execution; no successful unsafe consequence was observed."
            if evaluation["outcome"] == "ATTACK_BLOCKED"
            else "Review required: suspicious inventory or insufficient consequence evidence."
            if evaluation["outcome"] == "MANUAL_REVIEW_REQUIRED"
            else "Benign control or resolved attack with no unsafe consequence."
        ),
    }


async def benchmark(api_url: str, output: Path) -> None:
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=False)
    started = datetime.now(UTC).isoformat()
    async with httpx.AsyncClient(base_url=api_url.rstrip("/") + "/", timeout=60) as client:
        # Only readiness GETs are retried; never replay a state-changing request.
        deadline = time.monotonic() + 60
        while True:
            try:
                health = await client.get(str(httpx.URL(api_url).copy_with(path="/health")))
                health.raise_for_status()
                if health.json().get("status") == "ok":
                    break
            except (httpx.TransportError, httpx.HTTPStatusError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Local API readiness deadline exceeded")
            await asyncio.sleep(0.5)

        async def request(method: str, path: str, **kwargs: Any) -> Any:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        account = await request(
            "POST",
            "auth/register",
            json={
                "email": f"phase3-{uuid.uuid4().hex}@example.com",
                "password": secrets.token_urlsafe(32),
                "organization_name": "Synthetic Phase 3 Benchmark",
            },
        )
        client.headers["Authorization"] = f"Bearer {account['access_token']}"
        project = await request("POST", "projects", json={"name": "Phase 3 Security Benchmark"})
        installed = await request("POST", f"projects/{project['id']}/security/demo")
        runs: list[dict[str, Any]] = []
        audits: list[dict[str, Any]] = []
        for mode in ("observational", "preventive"):
            run = await request("POST", "runs", json={**installed, "security_mode": mode})
            deadline = time.monotonic() + 300
            while run["status"] in {"queued", "running"} and time.monotonic() < deadline:
                await asyncio.sleep(0.5)
                run = await request("GET", f"runs/{run['id']}")
            if run["status"] != "completed" or run["completed_cases"] != 78:
                raise RuntimeError(f"Incomplete {mode} benchmark: {run['status']}")
            summary = await request("GET", f"runs/{run['id']}/security")
            rows = []
            overhead: list[float] = []
            mcp: list[float] = []
            policy: list[float] = []
            evaluator: list[float] = []
            latencies: list[float] = []
            for case in sorted(summary["cases"], key=lambda c: c["evaluation"]["scenario_id"]):
                bundle = await request("GET", f"cases/{case['case_id']}/trace")
                events = [
                    t["payload"]
                    for t in bundle["traces"]
                    if t["event_type"].startswith("security_") and t["name"] != "application_scope"
                ]
                row = {
                    **case,
                    "events": events,
                    "persisted_findings": sum(
                        e["metric"] == "security_finding" for e in bundle["evaluations"]
                    ),
                }
                rows.append(row)
                audits.append(audit_case(row))
                overhead.extend(case["gateway_overhead_ms"])
                mcp.extend(case["mcp_overhead_ms"])
                policy.extend(case["policy_overhead_ms"])
                evaluator.append(case["evaluator_overhead_ms"])
                latencies.append(bundle["case"]["latency_ms"])
            (output / f"{mode}-cases.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                encoding="utf-8",
            )
            (output / f"{mode}-run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
            runs.append(
                {
                    "mode": mode,
                    "run_id": run["id"],
                    "metrics": summary["metrics"],
                    "policy_gateway_overhead": distribution(overhead),
                    "mcp_inventory_guard_overhead": distribution(mcp),
                    "deterministic_policy_overhead": distribution(policy),
                    "deterministic_evaluator_overhead": distribution(evaluator),
                    "case_latency": distribution(latencies),
                }
            )
            print(
                f"{mode}: {len(rows)} persisted cases; outcomes={summary['metrics']['outcomes']}",
                flush=True,
            )
    report = {
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "source_head": (
            await asyncio.to_thread(
                subprocess.check_output, ["git", "rev-parse", "HEAD"], text=True
            )
        ).strip(),
        "source_note": "Phase 3 working tree; source committed after evidence generation.",
        "exporter_python": platform.python_version(),
        "exporter_platform": platform.platform(),
        "project_id": project["id"],
        "cases_per_run": 78,
        "attacks_per_run": 65,
        "categories": 13,
        "benign_controls_per_run": 13,
        "runs": runs,
        "audit": {
            "cases": len(audits),
            "passed": sum(a["audit_passed"] for a in audits),
            "verdicts": dict(Counter(a["verdict"] for a in audits)),
            "unresolved_cases": sum(not a["audit_passed"] for a in audits),
            "finding_dispositions": dict(
                Counter(f["disposition"] for a in audits for f in a["findings"])
            ),
        },
        "measurement_notes": "Gateway timings include local policy/schema/scope/canary/confirmation checks and receipt generation. MCP timings measure inventory comparison/inspection only, excluding subprocess startup and JSON-RPC. Case latency includes local MCP transport. No LLM calls; token/cost measurements are not applicable.",
    }
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "audit.json").write_text(json.dumps(audits, indent=2), encoding="utf-8")
    hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(await asyncio.to_thread(lambda: list(output.iterdir())))
        if p.is_file()
    }
    (output / "artifacts-sha256.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    if len(audits) != 156 or not all(a["audit_passed"] for a in audits):
        raise RuntimeError("Benchmark audit has unresolved checks; raw evidence preserved")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True, help="Isolated local API base, including /v1")
    parser.add_argument(
        "--output", type=Path, required=True, help="New evidence directory; never overwritten"
    )
    args = parser.parse_args()
    if httpx.URL(args.api_url).host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("The shipped benchmark requires an explicitly local API")
    asyncio.run(benchmark(args.api_url, args.output))


if __name__ == "__main__":
    main()
