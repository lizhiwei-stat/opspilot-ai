# 第一阶段开发报告（Stage 1）

日期：2026-11-02（Asia/Hong_Kong）｜提交：见 tag `stage-1`

## 1. 环境检查结果（开发沙箱，Debian 13）

| 项 | 结果 |
|---|---|
| Python | 系统 3.13.5；项目用 uv 安装的 **3.11.17**（`uv sync -p 3.11`） |
| uv | 0.13.5 |
| Node | 系统 20.19.2 → jsdom 30 要求 Node ≥ 22.22，另装 **Node 22.23.4 LTS** 到 `/opt` 后构建与测试 |
| Docker | **不可用**（`docker: command not found`） |
| apt | 沙箱网络拦截 Debian 源（HTTP 500 / TLS EOF），无法 apt 安装 PostgreSQL |
| PostgreSQL + pgvector | 改用 PyPI `pgserver 0.1.4` 自带的 **PostgreSQL 16.2 + pgvector 0.6.2** 二进制，在 127.0.0.1:5432 启动真实服务 |
| GPU | 不需要 |

**诚实说明**：本阶段实际跑通的是"本地 PostgreSQL 16.2 + pgvector 0.6.2 进程 + uvicorn + Vite dev server"。`infra/docker-compose.yml`（PG17 + pgvector 0.8.7 镜像）、Dockerfile 和 GitHub Actions 只做了 YAML 解析校验，**没有真正运行过**。

## 2. 实际执行的命令与真实输出

### 2.1 数据库迁移（全新库）
```
$ alembic current
0001 (head)
$ alembic check
No new upgrade operations detected.
$ python scripts/seed_demo.py
seed complete, newly created: {'users': 3, 'knowledge_bases': 2, 'kb_permissions': 1}
$ psql \dx vector
                           List of installed extensions
  Name  | Version | Schema |                     Description                      
--------+---------+--------+------------------------------------------------------
 vector | 0.6.2   | public | vector data type and ivfflat and hnsw access methods
(1 row)

```
另外在开发中执行过 `alembic upgrade head → downgrade base → upgrade head` 完整往返；集成测试每次运行也会执行一次 downgrade/upgrade。

### 2.2 启动后端并检查健康
```
$ uvicorn opspilot.main:create_app --factory --port 8000
$ curl -s localhost:8000/health/live
{"status":"alive","version":"0.1.0"}
$ curl -s localhost:8000/health/ready
{"status":"ready","checks":[{"name":"database","ok":true,"latency_ms":6.51,"detail":"postgres 16.2"},{"name":"pgvector","ok":true,"latency_ms":1.73,"detail":"pgvector 0.6.2, cosine_distance_probe=1.0"},{"name":"migrations","ok":true,"latency_ms":0.41,"detail":"alembic revision 0001"},{"name":"llm","ok":true,"latency_ms":0.01,"detail":"mock/mock-llm-v1: offline deterministic mock"},{"name":"embedding","ok":true,"latency_ms":0.12,"detail":"mock/mock-hash-embedding-v1 dim=1024"}]}
HTTP 200
```
停掉数据库后的就绪检查（真实输出，节选）：
```
{"status":"not_ready","checks":[{"name":"database","ok":false,...,"detail":"OperationalError: ... port 5432 failed: Connection refused"}, ... {"name":"llm","ok":true,...},{"name":"embedding","ok":true,...}]}
HTTP 503
```
重启数据库后立即恢复 `HTTP 200`（`pool_pre_ping` 生效）。

登录与鉴权（curl 实测）：
```
$ POST /api/v1/auth/login (admin) → 200，GET /api/v1/auth/me →
{"id":"59d02f20-…","email":"admin@opspilot.local","display_name":"Ops Admin","role":"admin"}
$ POST /api/v1/auth/login 错误密码 →
{"error":{"code":"unauthorized","message":"Invalid email or password","request_id":"6b2fad6f…","details":null}} HTTP 401
```
结构化访问日志（真实一行）：
```
{"method": "GET", "path": "/api/v1/auth/me", "status": 200, "duration_ms": 6.72, "event": "http_request", "request_id": "b5831714651241398e88b352820c6e31", "level": "info", "timestamp": "2026-11-02T02:51:29.976520Z"}
```
前端经 Vite 代理访问后端：`curl localhost:5173/api/v1/system/version` 返回与后端一致的 JSON；无头 Chrome 截图见 `docs/assets/stage1-status.png`。

### 2.3 pytest（单元 + 连真实 PostgreSQL 的集成测试，`OPSPILOT_REQUIRE_DB=1`）
```
tests/integration/test_api.py::test_live PASSED                          [  3%]
tests/integration/test_api.py::test_request_id_propagated PASSED         [  6%]
tests/integration/test_api.py::test_ready_all_checks_pass PASSED         [  9%]
tests/integration/test_api.py::test_version PASSED                       [ 12%]
tests/integration/test_api.py::test_login_me_and_roles PASSED            [ 15%]
tests/integration/test_api.py::test_login_failures_are_uniform PASSED    [ 18%]
tests/integration/test_api.py::test_me_requires_valid_token PASSED       [ 21%]
tests/integration/test_api.py::test_error_envelope_validation_and_404 PASSED [ 25%]
tests/integration/test_schema.py::test_all_tables_and_extension PASSED   [ 28%]
tests/integration/test_schema.py::test_indexes_exist PASSED              [ 31%]
tests/integration/test_schema.py::test_vector_and_fulltext_search_roundtrip PASSED [ 34%]
tests/integration/test_schema.py::test_check_constraint_enforced PASSED  [ 37%]
tests/integration/test_schema.py::test_document_version_unique PASSED    [ 40%]
tests/unit/test_config.py::test_defaults_are_mock_and_secret_hidden PASSED [ 43%]
tests/unit/test_config.py::test_openai_provider_requires_key PASSED      [ 46%]
tests/unit/test_config.py::test_short_jwt_secret_rejected PASSED         [ 50%]
tests/unit/test_config.py::test_prod_rejects_dev_secret PASSED           [ 53%]
tests/unit/test_config.py::test_embedding_dim_locked_to_schema PASSED    [ 56%]
tests/unit/test_health_unit.py::test_readiness_reports_db_down_without_raising PASSED [ 59%]
tests/unit/test_llm.py::test_mock_llm_deterministic_and_counts_tokens PASSED [ 62%]
tests/unit/test_llm.py::test_mock_llm_rejects_empty PASSED               [ 65%]
tests/unit/test_llm.py::test_mock_embedding_normalised_and_semantic_overlap PASSED [ 68%]
tests/unit/test_llm.py::test_mock_embedding_empty_text_is_zero_vector PASSED [ 71%]
tests/unit/test_llm.py::test_factory_builds_mock_by_default PASSED       [ 75%]
tests/unit/test_llm.py::test_openai_compat_constructs_without_network PASSED [ 78%]
tests/unit/test_llm.py::test_openai_compat_wraps_errors PASSED           [ 81%]
tests/unit/test_logging.py::test_json_logs_redact_secrets PASSED         [ 84%]
tests/unit/test_security.py::test_password_hash_roundtrip PASSED         [ 87%]
tests/unit/test_security.py::test_jwt_roundtrip_and_claims PASSED        [ 90%]
tests/unit/test_security.py::test_jwt_tampered_or_wrong_secret_rejected PASSED [ 93%]
tests/unit/test_security.py::test_jwt_alg_none_rejected PASSED           [ 96%]
tests/unit/test_security.py::test_jwt_expired_rejected PASSED            [100%]
============================== 32 passed in 1.73s ==============================
```

### 2.4 Ruff
```
All checks passed!
50 files already formatted
```

### 2.5 前端构建与测试
```
computing gzip size...
dist/index.html                   0.41 kB │ gzip:  0.28 kB
dist/assets/index-DUo1Db9i.css   10.58 kB │ gzip:  2.95 kB
dist/assets/index--2eiSn_b.js   224.53 kB │ gzip: 70.56 kB

✓ built in 148ms
 Test Files  1 passed (1)
      Tests  2 passed (2)
```

### 2.6 开发过程中遇到并修复的问题
| 问题 | 原因 | 处理 |
|---|---|---|
| `alembic check` 报缺少外键 | `tickets ↔ ticket_drafts` 循环外键用了 `use_alter`，autogenerate 在 `create_table` 中静默丢弃 | 在迁移中两表创建后单独 `create_foreign_key`，downgrade 先删该约束；`alembic check` 通过 |
| 登录返回 422 | `EmailStr`（email-validator）拒绝 `.local` 等保留域名 | 改为简单格式校验，移除 email-validator 依赖 |
| Vitest 无法启动 worker | jsdom 30 / undici 需要 Node ≥ 22 | 统一要求 Node 22 LTS（`engines`、Dockerfile、CI） |
| Tailwind v4 边框为黑色 | v4 默认 `border-color: currentColor` | 在 base 层设为 slate-200 |
| alembic 弃用警告 | 缺少 `path_separator` | `alembic.ini` 加 `path_separator = os` |

## 3. 已实现 / 未实现

**已实现并有测试覆盖**
- 配置：pydantic-settings，`OPSPILOT_` 前缀；校验 JWT 密钥长度、prod 禁用默认密钥、openai_compatible 必须有 Key、向量维度必须为 1024；密钥用 `SecretStr`。
- 日志：structlog JSON，request_id（接受合法的 `X-Request-ID`，否则生成），password/token/api_key 等字段自动打码。
- 错误：统一 `{"error":{...}}`，覆盖 AppError、HTTP 404/405、422、未捕获异常（500 不泄露细节）。
- 数据库：16 张表的完整 schema（见 DATABASE.md），含 HNSW 向量索引、tsvector 生成列 + GIN、CHECK/UNIQUE 约束；测试验证了向量最近邻与全文检索在真实 pgvector 上可用。
- 健康检查：live / ready（5 项独立检查，失败 503）。
- Provider：`LLMProvider`/`EmbeddingProvider` 协议；MockLLM（确定性 + 近似 token 计数）、MockEmbedding（特征哈希，1024 维）、OpenAICompatLLM/Embedding（错误被包装为 `LLMError`；单元测试只连 127.0.0.1:9 验证错误包装，**未调用任何真实/付费 API**）。
- 认证：Argon2id 密码哈希、PyJWT HS256（拒绝 `alg=none`、过期、篡改、错误签发者），`/login`、`/me`、`require_role("admin")`；用户不存在时仍做一次哈希校验以平衡耗时，错误信息统一。
- 种子数据：3 个演示用户、2 个知识库（restricted / public）、1 条 ACL 授权；幂等。
- 前端：React 19 + TS + Vite 8 + Tailwind v4 状态页 + 登录，调用真实 `/health/ready`、`/api/v1/system/version`、`/auth/login`、`/auth/me`；2 个 Vitest 测试（503 渲染失败项、网络错误）。
- 工程：uv + `uv.lock`、Ruff、Makefile、`.env.example`、`.gitignore`、`scripts/local_pg.sh`（无 Docker 时的 PG+pgvector 方案，已实测 start → migrate → stop）。

**未实现（按计划在后续阶段）**
- 文档上传 / 解析 / 分块 / 检索 / 引用 / ACL 过滤查询（阶段 2）
- LangGraph Agent、工具调用、重试降级（阶段 3）
- MCP Server、审批 API、Checkpointer 恢复（阶段 4）
- 完整管理界面（阶段 5）
- 评测、Prompt Injection 测试、PII 脱敏、上传校验（阶段 6）
- LangChain / LangGraph / MCP SDK 尚未加入依赖（阶段 3/4 引入时再核对版本）
- Docker Compose 与 CI 未实际运行（见 §1）

## 4. 关键技术决策
见 [ARCHITECTURE.md §4](../ARCHITECTURE.md)。摘要：PostgreSQL + pgvector 一库承载业务、向量与全文；中文关键词检索采用"应用层分词 + `simple` tsvector"；同步 SQLAlchemy 2 + psycopg 3；向量 1024 维对齐 text-embedding-v4；Provider 协议 + 默认 Mock；PyJWT + Argon2；live/ready 分离且就绪检查不花 token；工单写入只能经服务端审批（数据库唯一约束 + 哈希校验）；TypeScript 选 5.9 稳定线而非 7.0。

## 5. 复现步骤
```bash
cd opspilot-ai
cp .env.example .env
uv sync -p 3.11
scripts/local_pg.sh start          # 或 docker compose -f infra/docker-compose.yml up -d postgres
uv run alembic upgrade head
uv run python scripts/seed_demo.py
make test                          # 期望：32 passed
make lint                          # 期望：All checks passed!
cd frontend && npm ci && npm run build && npm test   # 期望：2 passed（需 Node 22）
make dev-backend & (cd frontend && npm run dev)
curl -s localhost:8000/health/ready
```

## 6. 本阶段文件清单（git ls-files）
```
.env.example
.github/workflows/ci.yml
.gitignore
Makefile
README.md
alembic.ini
backend/migrations/env.py
backend/migrations/script.py.mako
backend/migrations/versions/20261102_0001_initial_schema.py
backend/src/opspilot/__init__.py
backend/src/opspilot/api/__init__.py
backend/src/opspilot/api/deps.py
backend/src/opspilot/api/routes/__init__.py
backend/src/opspilot/api/routes/auth.py
backend/src/opspilot/api/routes/health.py
backend/src/opspilot/api/routes/system.py
backend/src/opspilot/api/schemas.py
backend/src/opspilot/core/__init__.py
backend/src/opspilot/core/config.py
backend/src/opspilot/core/errors.py
backend/src/opspilot/core/logging.py
backend/src/opspilot/core/middleware.py
backend/src/opspilot/core/security.py
backend/src/opspilot/db/__init__.py
backend/src/opspilot/db/base.py
backend/src/opspilot/db/models.py
backend/src/opspilot/db/session.py
backend/src/opspilot/llm/__init__.py
backend/src/opspilot/llm/base.py
backend/src/opspilot/llm/factory.py
backend/src/opspilot/llm/mock.py
backend/src/opspilot/llm/openai_compat.py
backend/src/opspilot/main.py
backend/src/opspilot/repositories/__init__.py
backend/src/opspilot/repositories/users.py
backend/src/opspilot/services/__init__.py
backend/src/opspilot/services/auth.py
backend/src/opspilot/services/health.py
docs/API.md
docs/ARCHITECTURE.md
docs/DATABASE.md
docs/PRD.md
docs/PROGRESS.md
docs/reports/STAGE1_REPORT.md
docs/assets/stage1-status.png
evals/README.md
frontend/index.html
frontend/package-lock.json
frontend/package.json
frontend/src/App.test.tsx
frontend/src/App.tsx
frontend/src/api.ts
frontend/src/index.css
frontend/src/main.tsx
frontend/tsconfig.json
frontend/tsconfig.tsbuildinfo
frontend/vite.config.ts
infra/backend.Dockerfile
infra/docker-compose.yml
infra/frontend.Dockerfile
infra/postgres-init.sql
pyproject.toml
scripts/local_pg.sh
scripts/seed_demo.py
tests/__init__.py
tests/conftest.py
tests/integration/__init__.py
tests/integration/conftest.py
tests/integration/test_api.py
tests/integration/test_schema.py
tests/unit/__init__.py
tests/unit/test_config.py
tests/unit/test_health_unit.py
tests/unit/test_llm.py
tests/unit/test_logging.py
tests/unit/test_security.py
uv.lock
```
