import uuid

from apps.api.app.db.models import Agent, AgentVersion, Scenario, Severity
from apps.api.app.db.models import TestSuite as SuiteModel
from apps.api.app.services.runs import RunService


def test_snapshot_copies_mutable_configuration_and_omits_secrets() -> None:
    agent = Agent(id=uuid.uuid4(), project_id=uuid.uuid4(), name="Support", description="")
    version = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="v1",
        adapter_type="demo_support_agent",
        endpoint_url=None,
        model_provider="demo",
        model_name="model",
        system_prompt="private instructions",
        config={"temperature": 0},
        tool_registry=[{"name": "get_order"}],
    )
    suite = SuiteModel(
        id=uuid.uuid4(),
        project_id=agent.project_id,
        name="Golden",
        description="",
        version="1",
        gate_policy={"block_severities": ["critical"]},
    )
    scenario = Scenario(
        id=uuid.uuid4(),
        test_suite_id=suite.id,
        name="Lookup",
        input="Order ORD-1001",
        expected_output=None,
        expected_tools=["get_order"],
        forbidden_tools=[],
        expected_tool_arguments=None,
        tags=[],
        severity=Severity.MEDIUM,
        timeout_seconds=10,
        scenario_metadata={},
    )
    snapshot = RunService._snapshot(agent, version, suite, [scenario], {"max_cases": 1})
    version.config["temperature"] = 1
    scenario.input = "changed"
    assert snapshot["agent"]["config"]["temperature"] == 0
    assert snapshot["scenarios"][0]["input"] == "Order ORD-1001"
    assert "system_prompt" not in snapshot["agent"]
    assert "system_prompt_hash" in snapshot["agent"]
