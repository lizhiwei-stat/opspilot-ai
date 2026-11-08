"""Application settings (pydantic-settings). Business code never reads os.environ directly."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Key-loading policy (Stage 3 audit): OpsPilot reads configuration ONLY from its own process
# environment (OPSPILOT_* variables) and its own repo-root .env. The path is anchored to this
# repository, never the current working directory, so running a script from another
# project's directory cannot pick up that project's .env. No code reads other projects' files.
REPO_ROOT = Path(__file__).resolve().parents[4]
OWN_ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPSPILOT_",
        env_file=OWN_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        # Validation errors must never echo config values (DB URLs with passwords, API keys).
        hide_input_in_errors=True,
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
    # tolerated clock skew for iat/nbf/exp (WSL2 realtime clock steps back several seconds)
    jwt_clock_skew_s: int = Field(default=60, ge=0, le=300)

    llm_provider: Literal["mock", "openai_compatible"] = "mock"
    # mock = offline hash embedding (default) | local = free CPU sentence-transformers model
    # (needs `uv sync --group local-embed`) | openai_compatible = DashScope (PAID, guarded)
    embedding_provider: Literal["mock", "local", "openai_compatible"] = "mock"
    # Dim of the mock / openai_compatible model. Local models report their own dim.
    embedding_dim: int = Field(default=1024, ge=8, le=4096)
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v4"
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 2
    # Paid-API safety switch: openai_compatible providers refuse to build unless this is true.
    allow_paid_api: bool = False
    embedding_batch_size: int = Field(default=10, ge=1, le=64)

    # ---- local CPU models (Stage 2.5) ----
    local_embedding_model: str = "BAAI/bge-small-zh-v1.5"
    local_embedding_revision: str = "7999e1d3359715c523056ef9478215996d62a620"
    local_embedding_max_length: int = Field(default=512, ge=16, le=8192)
    local_embedding_batch_size: int = Field(default=16, ge=1, le=256)
    local_threads: int = Field(default=8, ge=1, le=64)
    # bge-*-zh-v1.5 recommend this prefix for short queries (not for passages); "" disables
    local_query_instruction: str = "为这个句子生成表示以用于检索相关文章："
    # rule = lexical-overlap-v1 (default, no model) | mock = identity (keeps fused order, tests)
    # | cross_encoder = real CPU cross-encoder (local-embed group)
    reranker: Literal["rule", "mock", "cross_encoder"] = "rule"
    reranker_model: str = "BAAI/bge-reranker-base"
    reranker_revision: str = "2cfc18c9415c912f9d8155881c133215df768a70"
    reranker_max_length: int = Field(default=512, ge=16, le=8192)
    # pgvector >= 0.8 HNSW iterative scan, see docs/reports/STAGE2_5_EVAL.md §iterative-scan
    hnsw_iterative_scan: Literal["off", "relaxed_order", "strict_order"] = "relaxed_order"
    hnsw_ef_search: int = Field(default=100, ge=10, le=1000)
    # When the embedding model is unavailable, vector/hybrid search falls back to keyword
    # only and flags the result as degraded (instead of failing the request).
    embedding_fallback_keyword: bool = True

    # ---- paid model gateway (Stage 2.5): OFF by default, see llm/gateway.py ----
    paid_approval_id: str = ""  # per-run approval token, required for any paid call
    paid_budget_cny: float = Field(default=0.0, ge=0, le=100)  # hard cap per run
    # Stage 3 pre-request guards (all checked BEFORE a paid request is sent)
    paid_max_calls: int = Field(default=0, ge=0, le=100000)  # per run; 0 = refuse all paid calls
    paid_max_output_tokens: int = Field(default=800, ge=1, le=8192)  # max_tokens is clamped
    paid_max_concurrency: int = Field(default=2, ge=1, le=16)  # in-flight paid requests
    paid_concurrency_wait_s: float = Field(default=5.0, ge=0, le=120)
    # ---- Stage 3 agent ----
    agent_llm_provider: Literal["mock", "qwen"] = "mock"
    agent_llm_model: str = "qwen-plus"
    agent_llm_temperature: float = Field(default=0.0, ge=0, le=2)
    agent_llm_max_tokens: int = Field(default=600, ge=16, le=8192)
    agent_llm_timeout_s: float = Field(default=30.0, ge=1, le=300)
    agent_max_steps: int = Field(default=24, ge=4, le=200)
    agent_run_timeout_s: float = Field(default=60.0, ge=1, le=600)  # per-turn wall clock
    agent_max_tool_calls: int = Field(default=6, ge=0, le=50)
    agent_max_react_rounds: int = Field(default=3, ge=0, le=20)
    agent_max_llm_calls: int = Field(default=8, ge=1, le=100)
    agent_recursion_limit: int = Field(default=40, ge=10, le=500)
    agent_tool_timeout_s: float = Field(default=5.0, ge=0.05, le=120)
    agent_tool_retries: int = Field(default=1, ge=0, le=5)  # read-only tools only
    agent_approval_ttl_hours: float = Field(default=24.0, gt=0, le=720)
    agent_checkpoint_schema: str = "langgraph"
    agent_llm_judge: bool = False  # real mode only: LLM second opinion in the evidence node
    agent_checkpoint_auto_setup: bool = True  # PostgresSaver.setup() on first agent use
    agent_fault_injection: bool = False  # eval/test only: lets scenarios inject tool faults
    # ---- MCP (Stage 4) ----
    # "mcp": agent tools execute in the 3 MCP servers (real protocol, stdio subprocesses);
    # "inprocess": Stage 3 direct calls (unit tests / fallback)
    agent_tool_transport: Literal["mcp", "inprocess"] = "mcp"
    mcp_call_timeout_s: float = Field(default=8.0, ge=0.1, le=120)
    mcp_connect_timeout_s: float = Field(default=20.0, ge=1, le=120)
    mcp_max_output_bytes: int = Field(default=65536, ge=1024, le=4 * 1024 * 1024)
    mcp_caller_token_ttl_s: int = Field(default=30, ge=5, le=300)
    mcp_max_revisions: int = Field(default=2, ge=0, le=5)  # return-for-revision rounds
    # Streamable HTTP (optional transport): loopback only, bearer token, Origin allowlist
    mcp_http_allowed_origins: tuple[str, ...] = ()
    model_ledger_path: str = "data/ledger/model_calls.jsonl"
    model_price_table: str = "configs/model_prices.json"
    eval_results_dir: str = "evals/results"  # Stage 5 eval browser (read-only)

    # ---- RAG (Stage 2) ----
    upload_dir: str = "data/uploads"
    max_upload_mb: float = Field(default=10.0, gt=0, le=100)
    chunk_size: int = Field(default=500, ge=100, le=4000)
    chunk_overlap: int = Field(default=80, ge=0, le=1000)
    rrf_k: int = Field(default=60, ge=1)
    retrieval_candidates: int = Field(default=50, ge=5, le=500)
    rerank_candidates: int = Field(default=20, ge=2, le=200)
    # Stage 2.5 fix (c): "replace" = order purely by reranker score (Stage 2 behaviour; lost
    # multi_doc gold chunks the reranker under-scores); "rrf" = fuse reranker rank with the
    # hybrid rank via RRF, so strong first-stage evidence survives a single-signal rerank.
    rerank_fusion: Literal["replace", "rrf"] = "rrf"
    # hosts an answer may link to (only if the link is also in trusted evidence)
    qa_link_allowlist: tuple[str, ...] = ("opspilot.local", "corp.local")
    rerank_fusion_weight: float = Field(default=0.5, ge=0.0, le=1.0)  # reranker share
    qa_min_evidence: float = Field(default=0.25, ge=0, le=1)  # dev-tuned (Stage 2: 0.5)
    qa_max_context_chunks: int = Field(default=4, ge=1, le=20)
    # Evidence signal: lexical = query-term coverage (Stage 2) | reranker = cross-encoder
    # relevance (only when OPSPILOT_RERANKER=cross_encoder) | auto = reranker if a model
    # reranker is active else lexical. Threshold for the reranker signal:
    qa_evidence: Literal["auto", "lexical", "reranker"] = "auto"
    qa_min_rerank_score: float = Field(default=0.02, ge=0, le=1)  # dev-tuned, bge-reranker-base
    # "Anchor" terms = Latin product/tech names in the question (Java, Redis, MySQL...). If one
    # does not occur in any evidence chunk the question is about something the KB does not
    # cover -> refuse (fixes the Stage 2 false accepts q045/q048).
    qa_require_anchor_terms: bool = True

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
        if needs_key and not self.allow_paid_api:
            raise ValueError(
                "openai_compatible providers call a paid API; set OPSPILOT_ALLOW_PAID_API=true "
                "explicitly (requires the owner's authorization)"
            )
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
