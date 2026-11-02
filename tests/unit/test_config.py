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


def test_embedding_dim_locked_to_schema() -> None:
    with pytest.raises(ValidationError, match="1024"):
        make_settings(embedding_dim=768)


def test_prod_rejects_env_example_placeholder() -> None:
    with pytest.raises(ValidationError, match="prod"):
        make_settings(env="prod", jwt_secret="change-me-to-a-long-random-string-at-least-32-chars")
