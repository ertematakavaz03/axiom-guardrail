from apps.api.app.config import Settings
from services.observability.langfuse import LangfusePilot


def test_langfuse_is_disabled_without_credentials() -> None:
    pilot = LangfusePilot(Settings())
    assert pilot.enabled is False
    pilot.emit_run(run_id="run", metrics={}, verdict="pass")
