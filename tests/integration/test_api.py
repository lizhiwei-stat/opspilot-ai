from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_live(client: TestClient) -> None:
    r = client.get("/health/live")
    assert r.status_code == 200 and r.json()["status"] == "alive"
    assert len(r.headers["x-request-id"]) == 32


def test_request_id_propagated(client: TestClient) -> None:
    r = client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    bad = client.get("/health/live", headers={"X-Request-ID": "bad id\n"})
    assert bad.headers["x-request-id"] != "bad id\n"


def test_ready_all_checks_pass(client: TestClient) -> None:
    r = client.get("/health/ready")
    body = r.json()
    assert r.status_code == 200, body
    assert body["status"] == "ready"
    assert {c["name"] for c in body["checks"]} == {
        "database",
        "pgvector",
        "migrations",
        "llm",
        "embedding",
    }
    assert all(c["ok"] for c in body["checks"])


def test_version(client: TestClient) -> None:
    body = client.get("/api/v1/system/version").json()
    assert body["stage"] == 1 and body["llm_provider"] == "mock"
    assert body["embedding_dim"] == 1024


def _login(client: TestClient, email: str, pw: str) -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_login_me_and_roles(client: TestClient, seeded: Session) -> None:
    import os

    admin = _login(client, "admin@opspilot.local", os.environ["OPSPILOT_SEED_ADMIN_PASSWORD"])
    user = _login(client, "ALICE@opspilot.local", os.environ["OPSPILOT_SEED_USER_PASSWORD"])
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {user}"}).json()
    assert me["email"] == "alice@opspilot.local" and me["role"] == "user"
    assert "password_hash" not in me
    ok = client.get("/api/v1/system/admin-ping", headers={"Authorization": f"Bearer {admin}"})
    assert ok.status_code == 200
    denied = client.get("/api/v1/system/admin-ping", headers={"Authorization": f"Bearer {user}"})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden"


def test_login_failures_are_uniform(client: TestClient, seeded: Session) -> None:
    a = client.post(
        "/api/v1/auth/login", json={"email": "admin@opspilot.local", "password": "wrong"}
    )
    b = client.post(
        "/api/v1/auth/login", json={"email": "nobody@opspilot.local", "password": "wrong"}
    )
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["message"] == b.json()["error"]["message"]


def test_me_requires_valid_token(client: TestClient) -> None:
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


def test_error_envelope_validation_and_404(client: TestClient) -> None:
    r = client.post("/api/v1/auth/login", json={"email": "not-an-email"})
    body = r.json()
    assert r.status_code == 422 and body["error"]["code"] == "validation_error"
    assert body["error"]["request_id"]
    nf = client.get("/nope")
    assert nf.status_code == 404 and nf.json()["error"]["code"] == "not_found"
