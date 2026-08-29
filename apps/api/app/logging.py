from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from apps.api.app.security.redaction import redact_secrets


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "organization_id", "project_id", "run_id", "case_id"):
            value = getattr(record, key, None)
            if value is not None:
                data[key] = str(value)
        if record.exc_info:
            data["exception_type"] = (
                record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
            )
        return json.dumps(redact_secrets(data), default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
