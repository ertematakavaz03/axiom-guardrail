import pytest
from pydantic import ValidationError

from apps.api.app.schemas.resources import AgentVersionCreate


def test_agent_config_rejects_plaintext_secret_keys() -> None:
    with pytest.raises(ValidationError, match="server-side secret references"):
        AgentVersionCreate(
            version="v1",
            adapter_type="generic_http",
            endpoint_url="https://agent.example/execute",
            config={"api_key": "plaintext"},
        )


def test_agent_endpoint_rejects_embedded_credentials() -> None:
    with pytest.raises(ValidationError, match="must not contain credentials"):
        AgentVersionCreate(
            version="v1",
            adapter_type="generic_http",
            endpoint_url="https://user:password@agent.example/execute",
        )
