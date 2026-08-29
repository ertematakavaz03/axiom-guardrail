from __future__ import annotations

import logging
from importlib import import_module
from typing import Any

from apps.api.app.config import Settings
from apps.api.app.security.redaction import redact_secrets
from services.orchestrator.graph import OrchestrationResult

logger = logging.getLogger(__name__)


class LangfusePilot:
    """Best-effort pilot; it is a no-op unless all Langfuse settings are present."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.langfuse_public_key
            and self.settings.langfuse_secret_key
            and self.settings.langfuse_host
        )

    def emit_case(
        self,
        *,
        run_id: str,
        case_id: str,
        scenario_name: str,
        result: OrchestrationResult,
    ) -> None:
        if not self.enabled:
            return
        client = self._client()
        if client is None:
            return
        try:
            with client.start_as_current_observation(
                as_type="span",
                name="agentarena.case",
                input={"run_id": run_id, "case_id": case_id, "scenario": scenario_name},
                metadata={"run_id": run_id, "case_id": case_id},
            ) as case_span:
                with client.start_as_current_observation(
                    as_type="span", name="agent.execute"
                ) as agent_span:
                    agent_events = [
                        trace.payload
                        for trace in result.traces
                        if trace.event_type in {"agent_input", "agent_output"}
                    ]
                    agent_span.update(output=redact_secrets(agent_events))
                for trace in result.traces:
                    if trace.event_type not in {"tool_completed", "tool_policy_decision"}:
                        continue
                    with client.start_as_current_observation(
                        as_type="tool", name=f"tool.{trace.name or 'unknown'}"
                    ) as tool_span:
                        tool_span.update(output=redact_secrets(trace.payload))
                case_span.update(
                    output={
                        "verdict": result.verdict,
                        "score": result.score,
                        "reason_codes": result.reason_codes,
                    }
                )
            client.flush()
        except Exception:
            logger.warning(
                "langfuse.case_export_failed",
                extra={"run_id": run_id, "case_id": case_id},
                exc_info=True,
            )

    def emit_run(self, *, run_id: str, metrics: dict[str, Any], verdict: str) -> None:
        if not self.enabled:
            return
        client = self._client()
        if client is None:
            return
        try:
            with client.start_as_current_observation(
                as_type="span",
                name="agentarena.run",
                input={"run_id": run_id},
                metadata={"run_id": run_id},
            ) as span:
                span.update(output={"verdict": verdict, "metrics": redact_secrets(metrics)})
            client.flush()
        except Exception:
            logger.warning("langfuse.run_export_failed", extra={"run_id": run_id}, exc_info=True)

    def _client(self) -> Any | None:
        try:
            langfuse = import_module("langfuse")
            public_key = self.settings.langfuse_public_key
            secret_key = self.settings.langfuse_secret_key
            if public_key is None or secret_key is None:
                return None

            return langfuse.Langfuse(
                public_key=public_key.get_secret_value(),
                secret_key=secret_key.get_secret_value(),
                host=self.settings.langfuse_host,
            )
        except Exception:
            logger.warning("langfuse.initialization_failed", exc_info=True)
            return None
