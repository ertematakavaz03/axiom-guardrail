from __future__ import annotations

import base64
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from jsonschema import Draft202012Validator

from services.security.models import Action, PolicyDecision, Principal, SecurityPolicy, digest


def pointer(value: Any, path: str) -> Any:
    if not path.startswith("/"):
        return None
    for part in path[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return None
    return value


def canary_matches(value: Any, canary: str) -> list[str]:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    variants = {
        "literal": canary,
        "url": quote(canary, safe=""),
        "base64": base64.b64encode(canary.encode()).decode(),
        "hex": canary.encode().hex(),
    }
    return [encoding for encoding, encoded in variants.items() if encoded in text]


@dataclass(frozen=True)
class ConfirmationGrant:
    principal_hash: str
    action_hash: str
    expires_at: float


class ConfirmationStore:
    """Trusted host-owned grant store. Never populate this from agent output.

    The local runner owns one store per case. Long-lived deployments must persist
    grants with atomic consumption; this object is not a distributed grant store.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._grants: dict[str, ConfirmationGrant] = {}

    def issue(self, principal: Principal, action: Action, ttl_seconds: float = 60) -> str:
        if ttl_seconds <= 0 or ttl_seconds > 300:
            raise ValueError("Confirmation lifetime must be in (0, 300] seconds")
        grant_id = secrets.token_urlsafe(24)
        self._grants[grant_id] = ConfirmationGrant(
            digest(principal.model_dump(mode="json")),
            action.fingerprint(),
            self.clock() + ttl_seconds,
        )
        return grant_id

    def valid(self, grant_id: str | None, principal: Principal, action: Action) -> bool:
        grant = self._grants.get(grant_id or "")
        return bool(
            grant
            and grant.expires_at > self.clock()
            and grant.action_hash == action.fingerprint()
            and grant.principal_hash == digest(principal.model_dump(mode="json"))
        )

    def consume(self, grant_id: str | None, principal: Principal, action: Action) -> bool:
        if not self.valid(grant_id, principal, action):
            return False
        self._grants.pop(grant_id or "")
        return True


class PolicyEngine:
    def __init__(self, policy: SecurityPolicy) -> None:
        self.policy = policy.model_copy(deep=True)
        self.policy_hash = digest(self.policy.model_dump(mode="json"))
        self.overhead_ms: list[float] = []
        self.validators: dict[str, Draft202012Validator] = {}
        for name, rule in self.policy.tools.items():
            Draft202012Validator.check_schema(rule.input_schema)
            # Remote references could make validation fetch untrusted resources.
            if '"$ref"' in json.dumps(rule.input_schema) or '"$dynamicRef"' in json.dumps(
                rule.input_schema
            ):
                raise ValueError("Policy schemas must be self-contained without references")
            self.validators[name] = Draft202012Validator(rule.input_schema)

    def check(
        self,
        action: Action,
        principal: Principal,
        *,
        call_count: int = 1,
        failures: int = 0,
        confirmation_valid: bool = False,
    ) -> PolicyDecision:
        started = time.perf_counter_ns()
        policy = self.policy
        reasons: list[str] = []
        evidence: dict[str, Any] = {
            "action": action.model_dump(),
            "principal": principal.model_dump(mode="json"),
            "call_count": call_count,
            "max_tool_calls": policy.max_tool_calls,
            "failures": failures,
            "max_retries": policy.max_retries,
        }
        name = f"{action.server}::{action.tool}" if action.server else action.tool
        if call_count > policy.max_tool_calls:
            reasons.append("RUNAWAY_TOOL_LOOP")
        if failures > policy.max_retries:
            reasons.append("UNSAFE_FAILURE_RECOVERY")
        if (
            action.tool in policy.forbidden_tools
            or name in policy.forbidden_tools
            or (policy.allowed_tools is not None and name not in policy.allowed_tools)
        ):
            reasons.append("UNAUTHORIZED_TOOL_CALL")
        if action.server:
            if (
                action.server not in policy.allowed_mcp_servers
                or action.server in policy.forbidden_mcp_servers
            ):
                reasons.append("FORBIDDEN_MCP_SERVER")
            if action.operation not in policy.allowed_mcp_operations:
                reasons.append("FORBIDDEN_MCP_RESOURCE")
            if action.operation == "tools/call":
                if action.tool not in policy.allowed_mcp_tools.get(
                    action.server, []
                ) or action.tool in policy.forbidden_mcp_tools.get(action.server, []):
                    reasons.append("FORBIDDEN_MCP_TOOL")
            else:
                allowed = (
                    policy.allowed_mcp_resources
                    if action.operation == "resources/read"
                    else policy.allowed_mcp_prompts
                )
                if action.tool not in allowed.get(action.server, []):
                    reasons.append("FORBIDDEN_MCP_RESOURCE")
        rule = policy.tools.get(name)
        argument_reason = (
            "MCP_ARGUMENT_POLICY_VIOLATION" if action.server else "ARGUMENT_POLICY_VIOLATION"
        )
        needs_confirmation = False
        if rule is None:
            reasons.append("FORBIDDEN_MCP_TOOL" if action.server else "UNAUTHORIZED_TOOL_CALL")
        else:
            errors = sorted(
                self.validators[name].iter_errors(action.arguments), key=lambda e: str(e.path)
            )
            if errors:
                reasons.append(argument_reason)
                evidence["schema_errors"] = [
                    {"path": list(e.absolute_path), "validator": e.validator} for e in errors
                ]
            if rule.risk == "R3":
                reasons.append("UNAUTHORIZED_TOOL_CALL")
            if rule.required_permissions - principal.permissions:
                reasons.append("AUTHORIZATION_BYPASS")
                evidence["missing_permissions"] = sorted(
                    rule.required_permissions - principal.permissions
                )
            for path, attribute in rule.scope_bindings.items():
                if pointer(action.arguments, path) != getattr(principal, attribute):
                    reasons.append(
                        {"tenant_id": "CROSS_TENANT_ACCESS", "user_id": "CROSS_USER_ACCESS"}.get(
                            attribute, "AUTHORIZATION_BYPASS"
                        )
                    )
                    evidence.setdefault("scope_violations", []).append(
                        {"path": path, "attribute": attribute}
                    )
            for path, resources in rule.resource_bindings.items():
                if pointer(action.arguments, path) not in resources:
                    reasons.append("AUTHORIZATION_BYPASS")
                    evidence.setdefault("resource_violations", []).append(path)
            if rule.mutates and not policy.allow_mutations:
                reasons.append("EXCESSIVE_AGENCY")
            for path in rule.destination_paths:
                if pointer(action.arguments, path) not in policy.allowed_destinations:
                    reasons.append(argument_reason)
                    evidence.setdefault("destination_violations", []).append(path)
            needs_confirmation = rule.requires_confirmation or rule.risk == "R2"
            if needs_confirmation and not confirmation_valid:
                reasons.append("CONFIRMATION_BYPASS")
        for sensitive in policy.sensitive_data:
            sink = f"tool:{name}"
            matches = canary_matches(action.arguments, sensitive.value)
            if matches and sink not in sensitive.allowed_sinks:
                reasons.append("DATA_EXFILTRATION")
                evidence.setdefault("canary_matches", []).append(
                    {"id": sensitive.id, "sink": sink, "encodings": matches}
                )
        reasons = list(dict.fromkeys(reasons))
        decision = "ALLOW"
        if reasons:
            decision = "REQUIRE_CONFIRMATION" if reasons == ["CONFIRMATION_BYPASS"] else "BLOCK"
        evidence["requires_confirmation"] = needs_confirmation
        result = PolicyDecision(
            decision=decision, reasons=reasons, evidence=evidence, policy_hash=self.policy_hash
        )
        self.overhead_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        return result
