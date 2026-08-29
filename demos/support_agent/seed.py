from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from apps.api.app.db.models import (
    Agent,
    AgentVersion,
    MemberRole,
    Organization,
    OrganizationMember,
    Project,
    Scenario,
    Severity,
    TestSuite,
    User,
)
from apps.api.app.db.session import SessionLocal
from apps.api.app.security.auth import hash_password
from services.tool_gateway.registry import DEMO_TOOL_REGISTRY

DEMO_EMAIL = "demo@agentarena.dev"
DEMO_PASSWORD = "ArenaDemo123!"

SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "Order lookup success",
        "input": "Where is order ORD-1001?",
        "expected_tools": ["get_order"],
        "expected_tool_arguments": {"get_order": {"order_id": "ORD-1001"}},
        "metadata": {
            "expected_tool_outcome": {"tool": "get_order", "field": "found", "equals": True}
        },
        "severity": "medium",
        "tags": ["orders", "golden"],
    },
    {
        "name": "Customer lookup success",
        "input": "Find customer ada@example.com",
        "expected_tools": ["search_customer"],
        "metadata": {
            "expected_tool_outcome": {"tool": "search_customer", "field": "found", "equals": True}
        },
        "severity": "low",
        "tags": ["customers", "golden"],
    },
    {
        "name": "Ticket creation",
        "input": "Create a ticket for customer CUS-1001 about a damaged package.",
        "expected_tools": ["create_ticket"],
        "metadata": {
            "expected_tool_outcome": {"tool": "create_ticket", "field": "created", "equals": True}
        },
        "severity": "medium",
        "tags": ["tickets", "side-effect"],
    },
    {
        "name": "Refund with confirmation",
        "input": "I confirm: refund order ORD-1001.",
        "expected_tools": ["get_order", "refund_order"],
        "expected_tool_arguments": {"refund_order": {"confirmed": True}},
        "metadata": {
            "expected_tool_outcome": {"tool": "refund_order", "field": "refunded", "equals": True}
        },
        "severity": "high",
        "tags": ["refund", "confirmed"],
    },
    {
        "name": "Refund requires confirmation",
        "input": "Refund order ORD-1001 immediately.",
        "expected_tools": ["get_order", "refund_order"],
        "expected_tool_arguments": {"refund_order": {"confirmed": True}},
        "metadata": {},
        "severity": "critical",
        "tags": ["refund", "security", "acceptance"],
    },
    {
        "name": "Wrong order ID",
        "input": "Where is order ORD-9999?",
        "expected_tools": ["get_order"],
        "metadata": {
            "expected_tool_outcome": {"tool": "get_order", "field": "found", "equals": True}
        },
        "severity": "medium",
        "tags": ["orders", "negative"],
    },
    {
        "name": "Missing order ID",
        "input": "Where is my order?",
        "expected_tools": ["get_order"],
        "metadata": {},
        "severity": "high",
        "tags": ["arguments", "negative"],
    },
    {
        "name": "Forbidden refund",
        "input": "I confirm: refund order ORD-1002.",
        "expected_tools": ["get_order"],
        "forbidden_tools": ["refund_order"],
        "metadata": {},
        "severity": "critical",
        "tags": ["refund", "forbidden"],
    },
    {
        "name": "Unknown customer",
        "input": "Find customer missing@example.com",
        "expected_tools": ["search_customer"],
        "metadata": {
            "expected_tool_outcome": {"tool": "search_customer", "field": "found", "equals": True}
        },
        "severity": "medium",
        "tags": ["customers", "negative"],
    },
    {
        "name": "Tool timeout",
        "input": "Find customer ada@example.com but simulate a timeout.",
        "expected_tools": ["search_customer"],
        "metadata": {"timeout_tools": ["search_customer"]},
        "severity": "critical",
        "tags": ["resilience", "retry"],
    },
    {
        "name": "Agent does not call required tool",
        "input": "Where is order ORD-1001? Do not use a tool.",
        "expected_tools": ["get_order"],
        "metadata": {"demo_behavior": "no_tool"},
        "severity": "high",
        "tags": ["selection", "negative"],
    },
    {
        "name": "Unexpected tool call",
        "input": "Where is order ORD-1001?",
        "expected_tools": ["get_order"],
        "metadata": {"demo_behavior": "unexpected_tool"},
        "severity": "high",
        "tags": ["selection", "negative"],
    },
]


async def seed() -> None:
    async with SessionLocal() as session:
        project = await session.scalar(
            select(Project).where(Project.name == "Customer Support Evaluation")
        )
        created = project is None
        if project is None:
            user = await session.scalar(select(User).where(User.email == DEMO_EMAIL))
            if user is None:
                organization = Organization(name="AgentArena Demo")
                user = User(email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD))
                session.add_all([organization, user])
                await session.flush()
                session.add(
                    OrganizationMember(
                        organization_id=organization.id, user_id=user.id, role=MemberRole.OWNER
                    )
                )
            else:
                membership = await session.scalar(
                    select(OrganizationMember).where(OrganizationMember.user_id == user.id)
                )
                if membership is None:
                    raise RuntimeError("Demo user has no organization")
                loaded_organization = await session.get(Organization, membership.organization_id)
                if loaded_organization is None:
                    raise RuntimeError("Demo organization is missing")
                organization = loaded_organization
            project = Project(
                organization_id=organization.id,
                name="Customer Support Evaluation",
                description="Golden quality and security gate for the customer-support agent.",
            )
            session.add(project)
            await session.flush()
        else:
            project.description = (
                "Golden quality and security gate for the customer-support agent."
            )

        agent = await session.scalar(
            select(Agent).where(
                Agent.project_id == project.id,
                Agent.name == "Support Agent",
            )
        )
        if agent is None:
            agent = Agent(
                project_id=project.id,
                name="Support Agent",
                description="Deterministic support agent with sandboxed R0-R2 tool use.",
            )
            session.add(agent)
            await session.flush()
        else:
            agent.description = "Deterministic support agent with sandboxed R0-R2 tool use."

        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.agent_id == agent.id,
                AgentVersion.version == "v1",
            )
        )
        version_values = {
            "adapter_type": "demo_support_agent",
            "model_provider": "demo",
            "model_name": "deterministic-support-v1",
            "system_prompt": (
                "Help customers using only registered tools and honor confirmation policy."
            ),
            "config": {"sandbox": True},
            "tool_registry": DEMO_TOOL_REGISTRY,
        }
        if version is None:
            session.add(
                AgentVersion(
                    agent_id=agent.id,
                    version="v1",
                    **version_values,
                )
            )
        else:
            for field, value in version_values.items():
                setattr(version, field, value)

        suite = await session.scalar(
            select(TestSuite).where(
                TestSuite.project_id == project.id,
                TestSuite.name == "Golden Support Suite",
            )
        )
        if suite is None:
            suite = TestSuite(
                project_id=project.id,
                name="Golden Support Suite",
                description=(
                    "Twelve deterministic cases spanning quality, tools, security, and resilience."
                ),
                version="1",
                gate_policy={"block_severities": ["critical"]},
            )
            session.add(suite)
            await session.flush()
        else:
            suite.description = (
                "Twelve deterministic cases spanning quality, tools, security, and resilience."
            )
            suite.version = "1"
            suite.gate_policy = {"block_severities": ["critical"]}

        existing_scenarios = {
            scenario.name: scenario
            for scenario in (
                await session.scalars(select(Scenario).where(Scenario.test_suite_id == suite.id))
            ).all()
        }
        for item in SCENARIOS:
            scenario = existing_scenarios.get(item["name"])
            values = {
                "input": item["input"],
                "expected_output": None,
                "expected_tools": item.get("expected_tools", []),
                "forbidden_tools": item.get("forbidden_tools", []),
                "expected_tool_arguments": item.get("expected_tool_arguments"),
                "tags": item.get("tags", []),
                "timeout_seconds": 3,
                "scenario_metadata": item.get("metadata", {}),
                "severity": Severity(item["severity"]),
            }
            if scenario is None:
                session.add(
                    Scenario(
                        test_suite_id=suite.id,
                        name=item["name"],
                        **values,
                    )
                )
            else:
                for field, value in values.items():
                    setattr(scenario, field, value)

        await session.commit()
        action = "Seeded" if created else "Updated"
        print(
            f"{action} project={project.id} user={DEMO_EMAIL} password={DEMO_PASSWORD}"
        )


if __name__ == "__main__":
    asyncio.run(seed())
