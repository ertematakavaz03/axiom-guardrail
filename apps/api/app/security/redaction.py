from __future__ import annotations

from typing import Any

SENSITIVE_PARTS = ("authorization", "api_key", "apikey", "token", "secret", "password", "cookie")
REDACTED = "[REDACTED]"


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_PARTS)


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): REDACTED if is_sensitive_key(str(key)) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    return value
