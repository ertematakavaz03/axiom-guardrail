from __future__ import annotations

import asyncio
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Literal

from pydantic import Field

from services.security.models import PolicyDecision, StrictModel, digest


class MCPInventory(StrictModel):
    server: str
    transport: str
    protocol_version: str = "2025-06-18"
    version: str | None = None
    risk: Literal["unclassified", "R0", "R1", "R2", "R3"] = "unclassified"
    provenance: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)
    prompts: list[dict[str, Any]] = Field(default_factory=list)

    def fingerprint(self) -> str:
        value = self.model_dump()
        for field in ("tools", "resources", "prompts"):
            value[field] = sorted(value[field], key=lambda item: json.dumps(item, sort_keys=True))
        return digest(value)


_POISON = re.compile(
    r"(?:ignore (?:all |any |the )?(?:previous|prior|system|developer) instructions|"
    r"(?:send|upload|reveal|exfiltrate) (?:all |the )?(?:secrets|system prompt|api keys)|"
    r"override (?:the )?(?:policy|authorization)|you must (?:call|execute) )",
    re.I,
)


def inspect_content(result: Any) -> list[dict[str, Any]]:
    """Inspect returned text as untrusted data; a match never proves success."""
    issues: list[dict[str, Any]] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "text" and isinstance(item, str):
                    match = _POISON.search(unicodedata.normalize("NFKC", item))
                    if match:
                        issues.append(
                            {
                                "reason_code": "MCP_CONTENT_POISONING",
                                "path": path + "/text",
                                "match": match.group(),
                                "classification": "suspicious_content_not_attack_success",
                            }
                        )
                else:
                    visit(item, path + "/" + key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, path + "/" + str(index))

    visit(result, "")
    return issues


def inspect_inventory(
    current: MCPInventory,
    approved: MCPInventory | None = None,
    peers: list[MCPInventory] | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if approved and current.fingerprint() != approved.fingerprint():
        before, after = approved.model_dump(), current.model_dump()
        issues.append(
            {
                "reason_code": "MCP_CAPABILITY_DRIFT",
                "approved_hash": approved.fingerprint(),
                "observed_hash": current.fingerprint(),
                "changed_fields": [key for key in before if before[key] != after[key]],
            }
        )
    for surface in ("tools", "resources", "prompts"):
        for definition in getattr(current, surface):
            text = str(definition.get("description", ""))
            match = _POISON.search(unicodedata.normalize("NFKC", text))
            if match:
                issues.append(
                    {
                        "reason_code": "MCP_TOOL_POISONING",
                        "surface": surface,
                        "definition": definition,
                        "match": match.group(),
                        "classification": "suspicious_content_not_attack_success",
                    }
                )
    for tool in current.tools:
        name = str(tool.get("name", ""))
        for peer in peers or []:
            if peer.server == current.server:
                continue
            for other in peer.tools:
                other_name = str(other.get("name", ""))
                normalized = unicodedata.normalize("NFKC", name).casefold()
                other_normalized = unicodedata.normalize("NFKC", other_name).casefold()
                ratio = SequenceMatcher(None, normalized, other_normalized).ratio()
                if (
                    ratio >= 0.88
                    and name != other_name
                    and (not name.isascii() or approved is None)
                ):
                    issues.append(
                        {
                            "reason_code": "MCP_TOOL_SHADOWING",
                            "tool": f"{current.server}::{name}",
                            "other_tool": f"{peer.server}::{other_name}",
                            "similarity": ratio,
                        }
                    )
    return issues


class MCPRegistry:
    """Approved snapshots are explicit; observing a new inventory never approves it."""

    def __init__(self, approved: list[MCPInventory] | None = None) -> None:
        self.approved = {item.server: item.model_copy(deep=True) for item in approved or []}

    def guard(self, observed: MCPInventory) -> PolicyDecision:
        approved = self.approved.get(observed.server)
        issues = inspect_inventory(observed, approved, list(self.approved.values()))
        if approved is None:
            issues.insert(
                0,
                {
                    "reason_code": "FORBIDDEN_MCP_SERVER",
                    "server": observed.server,
                    "explanation": "No approved inventory",
                },
            )
        return PolicyDecision(
            decision="BLOCK" if issues else "ALLOW",
            reasons=list(dict.fromkeys(item["reason_code"] for item in issues)),
            evidence={"inventory": observed.model_dump(), "issues": issues},
            policy_hash=approved.fingerprint() if approved else "unregistered",
        )


class LocalMCPClient:
    """Bounded stdio client for the shipped synthetic fixture only.

    No caller-supplied command or network endpoint can be launched. This is a
    deterministic test transport, not a general-purpose MCP/OAuth client.
    """

    def __init__(self, profile: str = "benign", revision: int = 1) -> None:
        if profile not in {"benign", "adversarial"} or revision not in {1, 2}:
            raise ValueError("Unknown local MCP fixture")
        self.profile, self.revision = profile, revision
        self.process: asyncio.subprocess.Process | None = None
        self.transcript: list[dict[str, Any]] = []
        self._sequence = 0
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> LocalMCPClient:
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "demos.security_lab.mcp_server",
            self.profile,
            str(self.revision),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=1_048_576,
        )
        try:
            result = await self.request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "axiom-local-test", "version": "1"},
                },
                timeout_seconds=15,
            )
            if result.get("protocolVersion") != "2025-06-18":
                raise ValueError("Unsupported negotiated MCP protocol")
            await self.notify("notifications/initialized", {})
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self.process:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        assert self.process and self.process.stdin
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        self.transcript.append({"direction": "sent", "message": message})
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout_seconds: float = 3
    ) -> dict[str, Any]:
        async with self._lock:
            assert self.process and self.process.stdin and self.process.stdout
            self._sequence += 1
            message = {
                "jsonrpc": "2.0",
                "id": self._sequence,
                "method": method,
                "params": params or {},
            }
            self.transcript.append({"direction": "sent", "message": message})
            self.process.stdin.write((json.dumps(message) + "\n").encode())
            await self.process.stdin.drain()
            async with asyncio.timeout(timeout_seconds):
                response = json.loads(await self.process.stdout.readline())
            self.transcript.append({"direction": "received", "message": response})
            if (
                response.get("id") != self._sequence
                or response.get("jsonrpc") != "2.0"
                or "error" in response
            ):
                raise ValueError("Invalid MCP response or protocol error")
            result = response.get("result")
            if not isinstance(result, dict):
                raise ValueError("Malformed MCP result")
            return result

    async def inventory(self) -> MCPInventory:
        initialization = self.transcript[1]["message"]["result"]
        return MCPInventory(
            server=f"local-{self.profile}",
            transport="stdio",
            version=initialization["serverInfo"]["version"],
            risk="R3" if self.profile == "adversarial" else "R1",
            provenance={"module": "demos.security_lab.mcp_server", "local_synthetic": True},
            capabilities=initialization["capabilities"],
            tools=(await self.request("tools/list"))["tools"],
            resources=(await self.request("resources/list"))["resources"],
            prompts=(await self.request("prompts/list"))["prompts"],
        )
