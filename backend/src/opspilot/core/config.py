"""Application settings (pydantic-settings). Business code never reads os.environ directly."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPSPILOT_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True
    database_url: str = "postgresql+psycopg://opspilot:opspilot@localhost:5432/opspilot"
    db_pool_size: int = 5
    db_connect_timeout_s: int = 3
    ready_timeout_s: float = Field(default=3.0, gt=0, le=30)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    jwt_secret: SecretStr = SecretStr("dev-only-insecure-secret-change-me-0123456789")
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_expire_minutes: int = 60

    llm_provider: Literal["mock", "openai_compatible"] = "mock"
    embedding_provider: Literal["mock", "openai_compatible"] = "mock"
    embedding_dim: int = 1024
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v4"
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 2

    @field_validator("embedding_dim")
    @classmethod
    def _dim_matches_schema(cls, v: int) -> int:
        # The chunks.embedding column is vector(1024); a different dim needs a migration.
        if v != 1024:
            raise ValueError("embedding_dim must be 1024 to match the vector(1024) column")
        return v

    @model_validator(mode="after")
    def _check_prod(self) -> Settings:
        secret = self.jwt_secret.get_secret_value()
        if self.env == "prod" and secret.startswith(("dev-only", "change-me")):
            raise ValueError("OPSPILOT_JWT_SECRET must be set in prod")
        if len(secret) < 32:
            raise ValueError("OPSPILOT_JWT_SECRET must be at least 32 characters")
        needs_key = "openai_compatible" in (self.llm_provider, self.embedding_provider)
        if needs_key and not self.llm_api_key.get_secret_value():
            raise ValueError("OPSPILOT_LLM_API_KEY is required for openai_compatible provider")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
