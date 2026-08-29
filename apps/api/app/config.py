from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AGENTARENA_",
        case_sensitive=False,
        extra="ignore",
        enable_decoding=False,
    )

    environment: str = "development"
    database_url: str = "postgresql+asyncpg://agentarena:agentarena@localhost:5432/agentarena"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: SecretStr = SecretStr("change-me-in-production")
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    worker_concurrency: int = 5
    max_scenario_input_length: int = 20_000
    max_cases_per_run: int = 100
    max_tool_calls_per_case: int = 10
    max_total_tokens: int = 100_000
    default_case_timeout_seconds: int = 30
    generic_http_secret: SecretStr | None = None
    model_pricing_json: dict[str, dict[str, float]] = Field(default_factory=dict)
    langfuse_public_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("LANGFUSE_PUBLIC_KEY", "AGENTARENA_LANGFUSE_PUBLIC_KEY"),
    )
    langfuse_secret_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("LANGFUSE_SECRET_KEY", "AGENTARENA_LANGFUSE_SECRET_KEY"),
    )
    langfuse_host: str | None = Field(
        default=None, validation_alias=AliasChoices("LANGFUSE_HOST", "AGENTARENA_LANGFUSE_HOST")
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("model_pricing_json", mode="before")
    @classmethod
    def parse_pricing(cls, value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value) if value else {}
        return value

    @model_validator(mode="after")
    def secure_production_secret(self) -> Settings:
        if (
            self.environment.lower() in {"production", "prod"}
            and self.jwt_secret.get_secret_value() == "change-me-in-production"
        ):
            raise ValueError("AGENTARENA_JWT_SECRET must be configured in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
