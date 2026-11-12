from opspilot import STAGE
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
    assert body["stage"] == STAGE and body["llm_provider"] == "mock"
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


def test_login_token_claims_match_user(client: TestClient, seeded: Session) -> None:
    import os

    from opspilot.core.security import decode_access_token
    from tests.conftest import make_settings

    r = client.post(
        "/api/v1/auth/login",
        json={"email": "bob@opspilot.local", "password": os.environ["OPSPILOT_SEED_USER_PASSWORD"]},
    )
    body = r.json()
    claims = decode_access_token(make_settings(), body["access_token"])
    assert claims["sub"] == body["user"]["id"] and claims["role"] == "user"
    assert body["expires_in"] == 3600 and body["token_type"] == "bearer"


def test_disabled_user_token_rejected(client: TestClient, seeded: Session) -> None:
    import os

    from sqlalchemy import update

    from opspilot.db.models import User

    token = _login(client, "bob@opspilot.local", os.environ["OPSPILOT_SEED_USER_PASSWORD"])
    seeded.execute(update(User).where(User.email == "bob@opspilot.local").values(is_active=False))
    seeded.commit()
    try:
        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        again = client.post(
            "/api/v1/auth/login",
            json={
                "email": "bob@opspilot.local",
                "password": os.environ["OPSPILOT_SEED_USER_PASSWORD"],
            },
        )
        assert again.status_code == 401
    finally:
        seeded.execute(
            update(User).where(User.email == "bob@opspilot.local").values(is_active=True)
        )
        seeded.commit()


def test_seed_creates_acl_and_is_idempotent(seeded: Session) -> None:
    from sqlalchemy import func, select

    from opspilot.db.models import KbPermission, KnowledgeBase, User

    before = [
        seeded.scalar(select(func.count()).select_from(t))
        for t in (User, KnowledgeBase, KbPermission)
    ]
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts/seed_demo.py"
    spec = importlib.util.spec_from_file_location("seed_again", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert mod.seed(seeded) == {"users": 0, "knowledge_bases": 0, "kb_permissions": 0, "tickets": 0}
    after = [
        seeded.scalar(select(func.count()).select_from(t))
        for t in (User, KnowledgeBase, KbPermission)
    ]
    assert before == after
    grant = seeded.execute(
        select(User.email, KnowledgeBase.slug, KbPermission.permission)
        .join(KbPermission, KbPermission.user_id == User.id)
        .join(KnowledgeBase, KnowledgeBase.id == KbPermission.kb_id)
    ).all()
    assert [tuple(g) for g in grant] == [("alice@opspilot.local", "order-platform", "read")]
    stored = seeded.scalar(select(User.password_hash).where(User.email == "alice@opspilot.local"))
    assert stored.startswith("$argon2id$")


def test_ready_returns_503_when_db_unreachable() -> None:
    from opspilot.main import create_app
    from tests.conftest import make_settings

    bad = make_settings(database_url="postgresql+psycopg://x:y@127.0.0.1:9/none_test")
    with TestClient(create_app(bad)) as c:
        r = c.get("/health/ready")
    body = r.json()
    assert r.status_code == 503 and body["status"] == "not_ready"
    failed = {ch["name"] for ch in body["checks"] if not ch["ok"]}
    assert failed == {"database", "pgvector", "migrations"}


def test_unhandled_error_does_not_leak_details(test_settings) -> None:  # type: ignore[no-untyped-def]
    from opspilot.main import create_app

    app = create_app(test_settings)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in r.text
