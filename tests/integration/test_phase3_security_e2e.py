from __future__ import annotations

import uuid

import pytest
from arq.connections import RedisSettings
from arq.worker import Worker
from httpx import AsyncClient

from apps.api.app.config import get_settings
from apps.api.app.main import app
from apps.api.app.workers.run_worker import execute_run
from demos.security_lab.corpus import corpus, demo_policy
from services.security.mcp import LocalMCPClient
from services.security.models import digest

pytestmark = pytest.mark.integration


async def account(client: AsyncClient) -> dict[str, str]:
    result = await client.post(
        "/v1/auth/register",
        json={
            "email": f"security-{uuid.uuid4().hex[:10]}@example.com",
            "password": "Integration123!",
            "organization_name": "Synthetic Security Test",
        },
    )
    assert result.status_code == 201, result.text
    return {"Authorization": f"Bearer {result.json()['access_token']}"}


async def test_security_api_worker_persistence_scoping_and_mcp(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await account(client)
    foreign = await account(client)
    project = (
        await client.post("/v1/projects", headers=headers, json={"name": "Security E2E"})
    ).json()
    prefix = f"/v1/projects/{project['id']}"
    assert (await client.get(prefix + "/security/policies", headers=foreign)).status_code == 404
    policy_response = await client.post(
        prefix + "/security/policies",
        headers=headers,
        json={"name": "Lab", "policy": demo_policy().model_dump(mode="json")},
    )
    assert policy_response.status_code == 201, policy_response.text
    policy = policy_response.json()
    assert policy["policy_hash"] == digest(policy["policy"])
    agent = (
        await client.post(prefix + "/agents", headers=headers, json={"name": "Security"})
    ).json()
    version = (
        await client.post(
            f"/v1/agents/{agent['id']}/versions",
            headers=headers,
            json={"version": "1", "adapter_type": "demo_security_agent"},
        )
    ).json()
    suite = (
        await client.post(prefix + "/suites", headers=headers, json={"name": "Security"})
    ).json()
    # Acceptance categories and benign MCP, using existing suite/scenario APIs.
    selected = [corpus()[index] for index in [0, 5, 15, 20, 25, 30, 35, 45, 60, 61, 63, 77]]
    for item in selected:
        response = await client.post(
            f"/v1/suites/{suite['id']}/scenarios",
            headers=headers,
            json={
                "name": item.name,
                "input": item.input,
                "metadata": {"security": item.model_dump(mode="json")},
            },
        )
        assert response.status_code == 201, response.text
    for profile in ["benign", "adversarial"]:
        async with LocalMCPClient(profile) as client_mcp:
            inventory = await client_mcp.inventory()
        registered = await client.post(
            prefix + "/security/mcp",
            headers=headers,
            json={"inventory": inventory.model_dump(), "approved": True},
        )
        assert registered.status_code == 201, registered.text
    async with LocalMCPClient(revision=2) as client_mcp:
        changed = await client_mcp.inventory()
    drift = await client.post(
        prefix + "/security/mcp", headers=headers, json={"inventory": changed.model_dump()}
    )
    assert "MCP_CAPABILITY_DRIFT" in [issue["reason_code"] for issue in drift.json()["issues"]]
    assert not drift.json()["approved"]
    for mode in ["observational", "preventive"]:
        created = await client.post(
            "/v1/runs",
            headers=headers,
            json={
                "project_id": project["id"],
                "test_suite_id": suite["id"],
                "agent_version_id": version["id"],
                "security_policy_id": policy["id"],
                "security_mode": mode,
            },
        )
        assert created.status_code == 202, created.text
        run = created.json()
        assert run["status"] == "queued"
        worker = Worker(
            [execute_run],
            redis_settings=RedisSettings.from_dsn(get_settings().redis_url),
            queue_name=app.state.redis.default_queue_name,
            burst=True,
            handle_signals=False,
        )
        try:
            await worker.async_run()
            assert worker.jobs_failed == 0
            assert worker.jobs_complete == 1
        finally:
            await worker.close()
        finished = (await client.get(f"/v1/runs/{run['id']}", headers=headers)).json()
        assert finished["status"] == "completed"
        assert finished["metrics"]["security"]["cases"] == len(selected)
        assert finished["metrics"]["security"]["execution_failures"] == 0
        assert finished["metrics"]["total_tokens"] is None
        assert finished["metrics"]["average_estimated_cost"] is None
        assert finished["overall_score"] is None
        summary = (await client.get(f"/v1/runs/{run['id']}/security", headers=headers)).json()
        assert len(summary["cases"]) == len(selected)
        for case in summary["cases"]:
            trace = (await client.get(f"/v1/cases/{case['case_id']}/trace", headers=headers)).json()
            raw = [
                row["payload"]
                for row in trace["traces"]
                if row["event_type"].startswith("security_") and row["name"] != "application_scope"
            ]
            assert digest(raw) == case["evaluation"]["evidence_hash"]
        if mode == "observational":
            assert summary["metrics"]["prevented_cases"] == 0
            assert "MCP_CONTENT_POISONING" in summary["metrics"]["reason_codes"]
        else:
            assert summary["metrics"]["prevented_cases"] > 0
        assert (
            await client.get(f"/v1/runs/{run['id']}/security", headers=foreign)
        ).status_code == 404
        case_id = summary["cases"][0]["case_id"]
        assert (
            await client.get(f"/v1/cases/{case_id}/security", headers=foreign)
        ).status_code == 404
        # Duplicate delivery cannot execute or add cases again.
        await execute_run({}, run["id"])
        assert len(
            (await client.get(f"/v1/runs/{run['id']}/cases", headers=headers)).json()
        ) == len(selected)
        assert (
            await client.post(f"/v1/runs/{run['id']}/resume", headers=foreign)
        ).status_code == 404
        assert (
            await client.post(f"/v1/runs/{run['id']}/resume", headers=headers)
        ).status_code == 409

    from services.orchestrator.graph import CaseOrchestrator

    async def unavailable(*args, **kwargs):
        raise TimeoutError("Synthetic infrastructure failure")

    monkeypatch.setattr(CaseOrchestrator, "run_case", unavailable)
    created = await client.post(
        "/v1/runs",
        headers=headers,
        json={
            "project_id": project["id"],
            "test_suite_id": suite["id"],
            "agent_version_id": version["id"],
            "security_policy_id": policy["id"],
        },
    )
    assert created.status_code == 202
    await execute_run({}, created.json()["id"])
    failed = (await client.get(f"/v1/runs/{created.json()['id']}", headers=headers)).json()
    assert failed["verdict"] == "block"
    metrics = failed["metrics"]["security"]
    assert metrics["total_cases"] == metrics["execution_failures"] == len(selected)
    assert metrics["evaluated_cases"] == metrics["prevented_cases"] == 0
    assert metrics["attack_success_rate"]["denominator"] == 0
