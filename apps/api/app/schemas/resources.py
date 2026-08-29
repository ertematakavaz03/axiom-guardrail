from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator

from apps.api.app.db.models import CaseStatus, RunStatus, Severity, Verdict
from apps.api.app.schemas.common import ORMModel
from apps.api.app.security.redaction import is_sensitive_key


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)


class ProjectResponse(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str
    created_at: datetime
    updated_at: datetime


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)


class AgentResponse(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str
    created_at: datetime
    updated_at: datetime


class AgentVersionCreate(BaseModel):
    version: str = Field(min_length=1, max_length=100)
    adapter_type: Literal["generic_http", "demo_support_agent"]
    endpoint_url: AnyHttpUrl | None = None
    model_provider: str = Field(default="demo", max_length=100)
    model_name: str = Field(default="deterministic-support-v1", max_length=200)
    system_prompt: str = Field(default="", max_length=50_000)
    config: dict[str, Any] = Field(default_factory=dict)
    tool_registry: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def endpoint_required_for_http(self) -> AgentVersionCreate:
        if self.adapter_type == "generic_http" and self.endpoint_url is None:
            raise ValueError("endpoint_url is required for generic_http")
        if self.endpoint_url is not None:
            parsed = urlsplit(str(self.endpoint_url))
            if parsed.username or parsed.password:
                raise ValueError("endpoint_url must not contain credentials")
            if any(is_sensitive_key(key) for key, _ in parse_qsl(parsed.query)):
                raise ValueError("endpoint_url must not contain secret query parameters")
        if _contains_sensitive_config_key(self.config):
            raise ValueError("config must use server-side secret references, not secret values")
        return self


class AgentVersionResponse(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    version: str
    adapter_type: str
    endpoint_url: str | None
    model_provider: str
    model_name: str
    system_prompt: str
    config: dict[str, Any]
    tool_registry: list[dict[str, Any]]
    created_at: datetime


class SuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    version: str = Field(default="1", min_length=1, max_length=100)
    gate_policy: dict[str, Any] = Field(default_factory=dict)


class SuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    version: str | None = Field(default=None, min_length=1, max_length=100)
    gate_policy: dict[str, Any] | None = None


class SuiteResponse(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str
    version: str
    gate_policy: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=250)
    input: str = Field(min_length=1, max_length=20_000)
    expected_output: str | None = Field(default=None, max_length=50_000)
    expected_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    expected_tool_arguments: dict[str, dict[str, Any]] | None = None
    tags: list[str] = Field(default_factory=list)
    severity: Severity = Severity.MEDIUM
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expected_tools", "forbidden_tools")
    @classmethod
    def tools_unique(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class ScenarioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=250)
    input: str | None = Field(default=None, min_length=1, max_length=20_000)
    expected_output: str | None = Field(default=None, max_length=50_000)
    expected_tools: list[str] | None = None
    forbidden_tools: list[str] | None = None
    expected_tool_arguments: dict[str, dict[str, Any]] | None = None
    tags: list[str] | None = None
    severity: Severity | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    metadata: dict[str, Any] | None = None


class ScenarioResponse(ORMModel):
    id: uuid.UUID
    test_suite_id: uuid.UUID
    name: str
    input: str
    expected_output: str | None
    expected_tools: list[str]
    forbidden_tools: list[str]
    expected_tool_arguments: dict[str, dict[str, Any]] | None
    tags: list[str]
    severity: Severity
    timeout_seconds: int
    metadata: dict[str, Any] = Field(validation_alias="scenario_metadata")
    created_at: datetime
    updated_at: datetime


class RunCreate(BaseModel):
    project_id: uuid.UUID
    test_suite_id: uuid.UUID
    agent_version_id: uuid.UUID
    budget: dict[str, int] | None = None


class RunResponse(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    test_suite_id: uuid.UUID
    agent_version_id: uuid.UUID
    status: RunStatus
    verdict: Verdict | None
    snapshot: dict[str, Any]
    metrics: dict[str, Any]
    overall_score: Decimal | None
    total_cases: int
    completed_cases: int
    passed_cases: int
    failed_cases: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class CaseResponse(ORMModel):
    id: uuid.UUID
    run_id: uuid.UUID
    scenario_id: uuid.UUID
    status: CaseStatus
    verdict: Verdict | None
    score: Decimal | None
    reason_codes: list[str] = Field(
        description="Causally ordered evaluator findings; the first item is the primary reason"
    )
    final_response: str | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    estimated_cost: Decimal | None
    started_at: datetime | None
    finished_at: datetime | None


class TraceResponse(ORMModel):
    id: uuid.UUID
    case_result_id: uuid.UUID
    sequence_number: int
    event_type: str
    name: str | None
    payload: dict[str, Any]
    duration_ms: int | None
    created_at: datetime


class EvalResultResponse(ORMModel):
    id: uuid.UUID
    case_result_id: uuid.UUID
    metric: str
    value: Decimal | None
    passed: bool
    reason_code: str | None
    explanation: str
    expected: Any
    actual: Any
    evidence: dict[str, Any]
    created_at: datetime


class CaseDetailResponse(CaseResponse):
    evaluations: list[EvalResultResponse]


class CaseTraceResponse(BaseModel):
    case: CaseResponse
    traces: list[TraceResponse]
    evaluations: list[EvalResultResponse]


class RunEvent(BaseModel):
    status: RunStatus
    completed_cases: int
    total_cases: int
    verdict: Verdict | None


def _contains_sensitive_config_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            is_sensitive_key(str(key)) or _contains_sensitive_config_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_config_key(item) for item in value)
    return False
