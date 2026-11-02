import json

from opspilot.core.logging import configure_logging, get_logger


def test_json_logs_redact_secrets(capsys) -> None:  # type: ignore[no-untyped-def]
    configure_logging("INFO", json=True)
    get_logger().info("evt", password="p@ss", api_key="sk-123", user="alice")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    data = json.loads(line)
    assert data["event"] == "evt" and data["user"] == "alice"
    assert data["password"] == "***" and data["api_key"] == "***"
