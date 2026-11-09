"""Stage 6: observability, redaction, KB delete, metrics, JWT/RBAC smoke."""

from __future__ import annotations

import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from tests.stage6.conftest import login

from opspilot.core.config import get_settings
from opspilot.core.redact import redact, register_secret
from opspilot.core.tracing import record_span, reset_for_tests, snapshot
from opspilot.db.models import KnowledgeBase


def test_redact_masks_registered_and_shaped_secrets() -> None:
    register_secret("super-secret-key-9999")
    text = (
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.aaa.bbb and key=super-secret-key-9999 "
        "sk-abcdefghijklmnopqrstuv"
    )
    out = redact(text)
    assert "super-secret-key-9999" not in out
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    assert "sk-abcdefghijklmnopqrstuv" not in out
    assert "***REDACTED***" in out


def test_tracing_snapshot_and_prometheus(api) -> None:
    reset_for_tests()
    record_span(name="unit", kind="http", trace_id="abc", status="200", duration_ms=12.5)
    snap = snapshot()
    assert snap["counters"]["http_requests_total"] >= 1
    r = api.get("/metrics")
    assert r.status_code == 200
    assert "opspilot_http_requests_total" in r.text
    assert "sk-" not in r.text


def test_admin_observability_requires_admin(api) -> None:
    assert api.get("/api/v1/admin/observability", headers=login(api, "alice")).status_code == 403
    r = api.get("/api/v1/admin/observability", headers=login(api, "admin"))
    assert r.status_code == 200
    body = r.json()
    assert "counters" in body and "recent_spans" in body and "durable" in body
    dumped = str(body)
    assert "OPSPILOT_LLM_API_KEY" not in dumped


def test_trace_headers_returned(api) -> None:
    r = api.get("/health/live", headers={"X-Request-ID": "req-stage6-1"})
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == "req-stage6-1"
    assert r.headers.get("x-trace-id")


def test_kb_delete_requires_confirm_and_owner(api) -> None:
    h = login(api, "admin")
    slug = f"del-{uuid.uuid4().hex[:8]}"
    created = api.post(
        "/api/v1/knowledge-bases",
        headers=h,
        json={"slug": slug, "name": "Temp", "description": "", "visibility": "public"},
    )
    assert created.status_code == 201, created.text
    kid = created.json()["id"]
    bad = api.request(
        "DELETE", f"/api/v1/knowledge-bases/{kid}", headers=h, json={"confirm_slug": "wrong"}
    )
    assert bad.status_code == 409
    forbid = api.request(
        "DELETE",
        f"/api/v1/knowledge-bases/{kid}",
        headers=login(api, "alice"),
        json={"confirm_slug": slug},
    )
    assert forbid.status_code == 403
    ok = api.request(
        "DELETE", f"/api/v1/knowledge-bases/{kid}", headers=h, json={"confirm_slug": slug}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "deleted"
    eng = create_engine(get_settings().database_url)
    with Session(eng) as sess:
        assert sess.get(KnowledgeBase, uuid.UUID(kid)) is None
    eng.dispose()


def test_settings_never_leak_api_key(api) -> None:
    r = api.get("/api/v1/admin/settings", headers=login(api, "admin"))
    assert r.status_code == 200
    body = r.json()
    dumped = str(body).lower()
    assert "api_key_configured" in str(body)
    assert "sk-" not in dumped
    assert "bearer " not in dumped
