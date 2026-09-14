from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from services.security.models import Action, PolicyDecision, Principal, SecurityEvent, digest
from services.security.policy import ConfirmationStore, PolicyEngine


class SecurityGateway:
    """Invoke the executor only after ALLOW. One gateway is owned by one case.

    Receipts are host-owned and must not be deserialized from an agent response.
    Calls are serialized so cumulative budgets and grant consumption are atomic.
    """

    def __init__(
        self,
        engine: PolicyEngine,
        principal: Principal,
        executor: Callable[[Action, Principal], Awaitable[Any]],
        *,
        confirmations: ConfirmationStore | None = None,
        timeout_seconds: float = 5,
    ) -> None:
        self.engine = engine
        self.principal = principal.model_copy(deep=True)
        self.executor = executor
        self.confirmations = confirmations or ConfirmationStore()
        self.timeout_seconds = timeout_seconds
        self.events: list[SecurityEvent] = []
        self.receipts: dict[str, str] = {}
        self.call_count = 0
        self.failures: dict[str, int] = {}
        self.overhead_ms: list[float] = []
        self._ids: set[str] = set()
        self._lock = asyncio.Lock()

    def record(self, kind: Any, payload: dict[str, Any]) -> SecurityEvent:
        event = SecurityEvent(
            id=f"gateway-{len(self.events) + 1}", kind=kind, payload=payload, source="gateway"
        )
        self.events.append(event)
        return event

    async def execute(self, action: Action, *, guard: PolicyDecision | None = None) -> Any:
        async with self._lock:
            if action.id in self._ids:
                raise ValueError("Duplicate action id")
            self._ids.add(action.id)
            # Copy before policy validation to prevent caller mutation while awaiting execution.
            action = action.model_copy(deep=True)
            started = time.perf_counter_ns()
            self.call_count += 1
            failure_key = f"{action.server or ''}::{action.tool}"
            self.record("action_requested", {"action": action.model_dump()})
            valid = self.confirmations.valid(action.confirmation_id, self.principal, action)
            decision = self.engine.check(
                action,
                self.principal,
                call_count=self.call_count,
                failures=self.failures.get(failure_key, 0),
                confirmation_valid=valid,
            )
            if guard and guard.decision != "ALLOW":
                decision = decision.model_copy(
                    update={
                        "decision": "BLOCK",
                        "reasons": list(dict.fromkeys(decision.reasons + guard.reasons)),
                        "evidence": {**decision.evidence, "mcp_guard": guard.model_dump()},
                    }
                )
            if decision.decision == "ALLOW" and decision.evidence.get("requires_confirmation"):
                if not self.confirmations.consume(action.confirmation_id, self.principal, action):
                    decision = decision.model_copy(
                        update={
                            "decision": "REQUIRE_CONFIRMATION",
                            "reasons": ["CONFIRMATION_BYPASS"],
                        }
                    )
            event = self.record(
                "policy_decision", {"action_id": action.id, **decision.model_dump()}
            )
            self.receipts[event.id] = digest(event.model_dump())
            self.overhead_ms.append((time.perf_counter_ns() - started) / 1_000_000)
            if decision.decision != "ALLOW":
                return {"blocked": True, "decision": decision.decision, "reasons": decision.reasons}
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    result = await self.executor(action, self.principal)
                if isinstance(result, dict) and result.get("isError"):
                    raise RuntimeError("MCP tool reported failure")
            except Exception as exc:
                self.failures[failure_key] = self.failures.get(failure_key, 0) + 1
                self.record(
                    "action_failed", {"action_id": action.id, "exception_type": type(exc).__name__}
                )
                return {"error": type(exc).__name__}
            self.failures[failure_key] = 0
            self.record("action_completed", {"action_id": action.id, "result": result})
            return result
