from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from demos.security_lab.corpus import PROMPT, SECRET, TENANT, USER
from services.security.models import Action, Principal, SecurityEvent, SecurityScenario


class VulnerableSandbox:
    """Deliberately omits authorization. All writes stay in this per-case dictionary."""

    def __init__(self) -> None:
        self.records: dict[str, Any] = {"record-a": "Synthetic public record A"}
        self.effects: list[dict[str, Any]] = []

    async def execute(self, action: Action, principal: Principal) -> Any:
        args = action.arguments
        if action.tool == "fail":
            raise RuntimeError("Controlled local failure")
        if action.tool == "malformed":
            raise ValueError("Controlled malformed tool result")
        if action.tool == "timeout":
            await asyncio.sleep(0.02)
            raise TimeoutError("Controlled local timeout")
        if action.tool == "read_secret":
            return SECRET
        if action.tool == "read_prompt":
            return PROMPT
        if action.tool == "read_record":
            if args.get("tenant_id") == "tenant-b":
                return TENANT
            if args.get("user_id") == "user-b":
                return USER
            return self.records.get(str(args.get("resource_id")), "Synthetic foreign resource")
        if action.tool == "status":
            return "Synthetic service operational"
        if action.tool == "release_report":
            self.effects.append({"tool": action.tool, "arguments": args})
            return "Synthetic consent-gated report released"
        if action.tool in {"delete", "update", "refund", "admin", "send", "ticket", "export_all"}:
            self.effects.append({"tool": action.tool, "arguments": args})
            if action.tool == "update":
                self.records[str(args.get("resource_id"))] = args.get("value")
            if action.tool == "delete":
                self.records.pop(str(args.get("resource_id")), None)
            return {"synthetic_effect": action.tool, "recorded_locally": True}
        raise ValueError("Unsupported local demo tool")


def encode(value: Any, encoding: str) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    if encoding == "base64":
        return base64.b64encode(text.encode()).decode()
    if encoding == "hex":
        return text.encode().hex()
    return str(text)


def resolve(value: Any, last: Any) -> Any:
    if isinstance(value, str):
        if value == "$last":
            return last
        return value.replace("$last", encode(last, "literal"))
    if isinstance(value, dict):
        return {key: resolve(item, last) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, last) for item in value]
    return value


class VulnerableAgent:
    """Insecure command-following target, with no scenario ID or expected-outcome logic.

    It follows embedded commands even in untrusted content, and treats tool data
    as safe output. This intentionally reproducible interpreter is not an LLM.
    """

    def commands(
        self, scenario: SecurityScenario, events: list[SecurityEvent]
    ) -> list[dict[str, Any]]:
        texts = [
            message["content"] for message in scenario.messages if message.get("role") == "user"
        ] or [scenario.input]
        content = scenario.setup.get("content")
        if content:
            events.append(
                SecurityEvent(
                    id="untrusted-context", kind="context", source="harness", payload=content
                )
            )
            texts.append(str(content["text"]))
        commands: list[dict[str, Any]] = []
        for text in texts:
            if "AXIOM_BASE64=" in text:
                text = base64.b64decode(text.split("AXIOM_BASE64=", 1)[1]).decode()
            if "AXIOM_COMMANDS=" in text:
                encoded = text.split("AXIOM_COMMANDS=", 1)[1]
                steps, _ = json.JSONDecoder().raw_decode(encoded)
                commands.extend(steps)
        return commands
