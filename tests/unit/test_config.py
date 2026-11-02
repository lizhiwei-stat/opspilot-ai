import pytest
from pydantic import ValidationError

from tests.conftest import make_settings


def test_defaults_are_mock_and_secret_hidden() -> None:
    s = make_settings()
    assert s.llm_provider == "mock"
    assert "test-secret" not in repr(s)


def test_openai_provider_requires_key() -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY"):
        make_settings(llm_provider="openai_compatible", llm_api_key="")


def test_short_jwt_secret_rejected() -> None:
    with pytest.raises(ValidationError, match="32"):
        make_settings(jwt_secret="short")


def test_prod_rejects_dev_secret() -> None:
    with pytest.raises(ValidationError, match="prod"):
        make_settings(env="prod", jwt_secret="dev-only-insecure-secret-change-me-0123456789")


def test_embedding_dim_no_longer_locked_but_bounded() -> None:
    # Stage 2.5: vectors live in chunk_embeddings (dimension-less column, one partial HNSW
    # index per model), so other dims are allowed; mismatch per model is checked at runtime.
    assert make_settings(embedding_dim=512).embedding_dim == 512
    with pytest.raises(ValidationError):
        make_settings(embedding_dim=0)


def test_prod_rejects_env_example_placeholder() -> None:
    with pytest.raises(ValidationError, match="prod"):
        make_settings(env="prod", jwt_secret="change-me-to-a-long-random-string-at-least-32-chars")


def test_validation_errors_do_not_echo_secrets() -> None:
    import pydantic

    from opspilot.core.config import Settings

    try:
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            embedding_provider="openai_compatible",
            llm_api_key="sk-should-never-appear",
            database_url="postgresql+psycopg://u:topsecretpw@h/db",
        )
    except pydantic.ValidationError as exc:
        text = str(exc)
        assert "sk-should-never-appear" not in text and "topsecretpw" not in text
    else:  # pragma: no cover
        raise AssertionError("expected paid-API guard to reject the config")
