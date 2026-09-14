from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Category(StrEnum):
    DIRECT_INJECTION = "direct_prompt_injection"
    INDIRECT_INJECTION = "indirect_prompt_injection"
    PROMPT_EXTRACTION = "system_prompt_extraction"
    TOOL_ABUSE = "tool_abuse"
    AUTHORIZATION = "authorization_bypass"
    CONFIRMATION = "confirmation_bypass"
    EXFILTRATION = "data_exfiltration"
    CROSS_TENANT = "cross_tenant_leakage"
    SECRETS = "secret_handling"
    AGENCY = "excessive_agency"
    RESOURCE = "resource_abuse"
    RECOVERY = "failure_recovery"
    MCP = "mcp_security"


class SecuritySeverity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Outcome(StrEnum):
    BLOCKED = "ATTACK_BLOCKED"
    FAILED = "ATTACK_FAILED"
    SUCCEEDED = "ATTACK_SUCCEEDED"
    NA = "NOT_APPLICABLE"
    REVIEW = "MANUAL_REVIEW_REQUIRED"


class Principal(StrictModel):
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    permissions: set[str] = Field(default_factory=set)


class SensitiveValue(StrictModel):
    id: str
    value: str = Field(min_length=12)
    kind: Literal["secret", "system_prompt", "tenant_data", "user_data"] = "secret"
    tenant_id: str | None = None
    user_id: str | None = None
    allowed_sinks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def synthetic_only(self) -> SensitiveValue:
        if not self.value.startswith("AXIOM_SYNTH_"):
            raise ValueError("Use explicitly synthetic AXIOM_SYNTH_ canaries only")
        return self


class Action(StrictModel):
    id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    server: str | None = None
    operation: Literal["tools/call", "resources/read", "prompts/get"] = "tools/call"
    confirmation_id: str | None = None

    def fingerprint(self) -> str:
        return digest(self.model_dump(exclude={"confirmation_id", "id"}))


class ToolRule(StrictModel):
    risk: Literal["R0", "R1", "R2", "R3"] = "R0"
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    required_permissions: set[str] = Field(default_factory=set)
    requires_confirmation: bool = False
    # JSON pointer -> trusted principal attribute. Missing arguments fail closed.
    scope_bindings: dict[str, Literal["tenant_id", "project_id", "user_id"]] = Field(
        default_factory=dict
    )
    resource_bindings: dict[str, list[str]] = Field(default_factory=dict)
    destination_paths: list[str] = Field(default_factory=list)
    mutates: bool = False


class SecurityPolicy(StrictModel):
    version: str = "1"
    tools: dict[str, ToolRule] = Field(default_factory=dict)
    allowed_tools: list[str] | None = None
    forbidden_tools: list[str] = Field(default_factory=list)
    allowed_mcp_servers: list[str] = Field(default_factory=list)
    forbidden_mcp_servers: list[str] = Field(default_factory=list)
    allowed_mcp_tools: dict[str, list[str]] = Field(default_factory=dict)
    forbidden_mcp_tools: dict[str, list[str]] = Field(default_factory=dict)
    allowed_mcp_resources: dict[str, list[str]] = Field(default_factory=dict)
    allowed_mcp_prompts: dict[str, list[str]] = Field(default_factory=dict)
    allowed_mcp_operations: list[str] = Field(default_factory=lambda: ["tools/call"])
    allowed_destinations: list[str] = Field(default_factory=list)
    max_tool_calls: int = Field(default=10, ge=1, le=1000)
    max_retries: int = Field(default=2, ge=0, le=100)
    allow_mutations: bool = False
    sensitive_data: list[SensitiveValue] = Field(default_factory=list)


class SecurityCondition(StrictModel):
    kind: Literal["response_contains", "tool_executed", "finding"]
    value: str = Field(min_length=1)


class SecurityScenario(StrictModel):
    id: str
    name: str
    description: str = ""
    security_category: Category
    attack_vector: str
    severity: SecuritySeverity = SecuritySeverity.HIGH
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    input: str
    messages: list[dict[str, str]] = Field(default_factory=list)
    setup: dict[str, Any] = Field(default_factory=dict)
    synthetic_sensitive_data: list[SensitiveValue] = Field(default_factory=list)
    allowed_tools: list[str] | None = None
    forbidden_tools: list[str] = Field(default_factory=list)
    required_authorization: dict[str, Any] = Field(default_factory=dict)
    required_confirmation: bool = False
    expected_safe_behavior: str = "Respect policy and keep protected data in scope"
    expected_block_behavior: str = "Deny unauthorized actions before execution"
    expected_evidence: list[str] = Field(default_factory=list)
    attack_success_conditions: list[SecurityCondition] = Field(default_factory=list)
    attack_failure_conditions: list[SecurityCondition] = Field(default_factory=list)
    cleanup_requirements: list[str] = Field(default_factory=lambda: ["Discard local sandbox"])
    tags: list[str] = Field(default_factory=list)
    is_attack: bool = True
    mutation_provenance: dict[str, Any] | None = None


class SecurityEvent(StrictModel):
    id: str
    kind: Literal[
        "input",
        "context",
        "action_requested",
        "action_completed",
        "action_failed",
        "policy_decision",
        "response",
        "log",
        "mcp_inventory",
        "error",
    ]
    payload: dict[str, Any]
    source: Literal["agent", "gateway", "mcp", "adapter", "harness"]


class PolicyDecision(StrictModel):
    decision: Literal["ALLOW", "BLOCK", "REQUIRE_CONFIRMATION"]
    reasons: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    policy_hash: str


class Finding(StrictModel):
    reason_code: str
    severity: SecuritySeverity
    description: str
    remediation: str
    event_ids: list[str] = Field(min_length=1)
    evidence: dict[str, Any]
    consequence: bool = False
    handling: Literal["DETECTED_ONLY", "PREVENTED"] = "DETECTED_ONLY"


class SecurityEvaluation(StrictModel):
    evaluator_version: str = "security-deterministic-1"
    scenario_id: str
    category: Category
    is_attack: bool
    outcome: Outcome
    verdict: Literal["pass", "warn", "block"]
    findings: list[Finding]
    evidence_hash: str
    evidence_complete: bool
    mode: Literal["observational", "preventive"]
