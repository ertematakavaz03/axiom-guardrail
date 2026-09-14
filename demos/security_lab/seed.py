from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import select

from apps.api.app.db.models import (
    Agent,
    AgentVersion,
    MCPRegistration,
    Project,
    Scenario,
    SecurityPolicyRecord,
    Severity,
    TestSuite,
)
from apps.api.app.db.session import SessionLocal
from demos.security_lab.corpus import corpus, demo_policy
from services.security.mcp import LocalMCPClient
from services.security.models import digest


async def seed(project_id: uuid.UUID) -> dict[str, str]:
    async with SessionLocal() as session:
        if await session.get(Project, project_id) is None:
            raise ValueError("Project does not exist")
        existing = await session.scalar(
            select(TestSuite).where(
                TestSuite.project_id == project_id, TestSuite.name == "Phase 3 Security Lab"
            )
        )
        if existing:
            raise ValueError(
                "Phase 3 suite already exists; use its immutable scenarios or create a new project"
            )
        agent = Agent(
            project_id=project_id,
            name="Local vulnerable security target",
            description="Synthetic command-following interpreter; deliberately insecure; no model API",
        )
        session.add(agent)
        await session.flush()
        version = AgentVersion(
            agent_id=agent.id,
            version="security-lab-v1",
            adapter_type="demo_security_agent",
            model_provider="synthetic",
            model_name="deterministic-vulnerable-interpreter",
            config={},
            tool_registry=[],
        )
        suite = TestSuite(
            project_id=project_id,
            name="Phase 3 Security Lab",
            description="65 distinct attacks and 13 benign controls; local synthetic evidence",
            version="1",
            gate_policy={},
        )
        policy = demo_policy().model_dump(mode="json")
        policy_record = SecurityPolicyRecord(
            project_id=project_id,
            agent_id=agent.id,
            name="Synthetic security lab policy v1",
            policy=policy,
            policy_hash=digest(policy),
        )
        session.add_all([version, suite, policy_record])
        await session.flush()
        for item in corpus():
            session.add(
                Scenario(
                    test_suite_id=suite.id,
                    name=item.name,
                    input=item.input,
                    expected_tools=[],
                    forbidden_tools=[],
                    tags=item.tags,
                    severity=Severity.HIGH if item.is_attack else Severity.LOW,
                    timeout_seconds=30,
                    scenario_metadata={"security": item.model_dump(mode="json")},
                )
            )
        for profile in ["benign", "adversarial"]:
            async with LocalMCPClient(profile) as client:
                inventory = await client.inventory()
            session.add(
                MCPRegistration(
                    project_id=project_id,
                    server=inventory.server,
                    inventory=inventory.model_dump(mode="json"),
                    inventory_hash=inventory.fingerprint(),
                    approved=True,
                )
            )
        await session.commit()
        return {
            "project_id": str(project_id),
            "agent_version_id": str(version.id),
            "test_suite_id": str(suite.id),
            "security_policy_id": str(policy_record.id),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=uuid.UUID, required=True)
    args = parser.parse_args()
    print(asyncio.run(seed(args.project_id)))


if __name__ == "__main__":
    main()
