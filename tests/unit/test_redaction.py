from apps.api.app.security.redaction import REDACTED, redact_secrets


def test_redacts_sensitive_keys_recursively() -> None:
    value = {
        "authorization": "Bearer private",
        "nested": {"api_key": "private", "safe": "visible"},
        "items": [{"password": "private"}],
    }
    result = redact_secrets(value)
    assert result["authorization"] == REDACTED
    assert result["nested"] == {"api_key": REDACTED, "safe": "visible"}
    assert result["items"][0]["password"] == REDACTED
