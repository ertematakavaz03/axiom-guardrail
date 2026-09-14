from pydantic import ValidationError
from pytest import MonkeyPatch

from apps.api.app.config import Settings


def test_compose_style_environment_values_parse(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTARENA_CORS_ORIGINS", "http://localhost:3000,http://web:3000")
    monkeypatch.setenv(
        "AGENTARENA_MODEL_PRICING_JSON",
        '{"demo:model":{"input_per_million":1,"output_per_million":2}}',
    )
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:3000", "http://web:3000"]
    assert settings.model_pricing_json["demo:model"]["output_per_million"] == 2
    assert settings.langfuse_public_key is not None


def test_production_rejects_default_jwt_secret(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTARENA_JWT_SECRET", raising=False)
    try:
        Settings(environment="production", _env_file=None)
    except ValidationError as error:
        assert "JWT_SECRET" in str(error)
    else:
        raise AssertionError("Production accepted the development JWT secret")
