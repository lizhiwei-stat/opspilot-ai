# OpsPilot AI

> Production-oriented AI Support & Ticket Resolution Platform
> 面向软件企业技术支持 / 研发 / 客服团队的智能技术支持 Agent 平台：查文档、查日志、给出有出处的故障建议、生成待审工单，人工审批后才落库。

**当前进度：第一阶段（基础设施）已完成，并在 WSL2 + Docker Compose 上实测（tag `stage-1.1`）** —— 详见 [docs/PROGRESS.md](docs/PROGRESS.md) 与 [阶段 1 报告](docs/reports/STAGE1_REPORT.md)。
Agent、RAG、MCP、审批等核心能力尚未实现，按阶段推进，README 只写已经验证过的内容。

![阶段 1 状态页（WSL2 Docker Compose 实测，Windows Edge 截图）](docs/assets/stage1-wsl-compose.png)

## 文档
- [PRD 需求说明](docs/PRD.md) · [架构说明](docs/ARCHITECTURE.md) · [数据库设计](docs/DATABASE.md) · [API 设计](docs/API.md)
- [阶段进度](docs/PROGRESS.md) · [阶段 1 报告](docs/reports/STAGE1_REPORT.md)

## 技术栈
FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · PostgreSQL + pgvector · PyJWT + Argon2 · structlog ·
（后续阶段）LangGraph · LangChain · MCP Python SDK · Qwen（OpenAI 兼容）· React 19 + TypeScript + Vite + Tailwind v4。
版本与选型依据见 [ARCHITECTURE.md §5](docs/ARCHITECTURE.md)。

## 快速开始（Windows + WSL2，从零开始）

前置（均为一次性安装，本项目不需要 sudo）：WSL2 Ubuntu、Docker（WSL 内 Docker Engine 或 Docker Desktop 的 WSL 集成）、Git、[uv](https://docs.astral.sh/uv/)。宿主机不需要 Node 和 Python（容器里自带；跑 pytest 时 uv 会自动下载 Python 3.11）。

```bash
# 1) 获取代码（任选：git clone / 解压发布包 / git bundle）
cd ~/projects/opspilot-ai

# 2) 配置：默认 Mock 模式，不需要任何 API Key
cp .env.example .env
#   - 端口冲突时改 .env 里的 OPSPILOT_PG_PORT / OPSPILOT_BACKEND_PORT / OPSPILOT_FRONTEND_PORT，
#     同时把 OPSPILOT_DATABASE_URL / OPSPILOT_TEST_DATABASE_URL 的端口改成与 OPSPILOT_PG_PORT 一致
#   - 建议生成随机 JWT 密钥：
sed -i "s/^OPSPILOT_JWT_SECRET=.*/OPSPILOT_JWT_SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')/" .env

# 3) 启动（postgres+pgvector → backend 自动 alembic upgrade head → frontend）
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
docker compose --env-file .env -f infra/docker-compose.yml ps

# 4) 演示账号（幂等，可重复执行）
docker compose --env-file .env -f infra/docker-compose.yml exec backend python scripts/seed_demo.py

# 5) 检查
curl -s http://localhost:8000/health/ready     # 端口按 .env 的 OPSPILOT_BACKEND_PORT
#    浏览器打开 http://localhost:5173 （Windows 浏览器可直接访问 WSL 的 localhost 端口）

# 6) 测试（在宿主 WSL 中，连接 compose 里的 opspilot_test 库）
uv sync --frozen
OPSPILOT_REQUIRE_DB=1 uv run pytest -v
uv run ruff check . && uv run ruff format --check .
docker compose --env-file .env -f infra/docker-compose.yml exec frontend npm test
```

> `--env-file .env` 不能省略：compose 文件位于 `infra/`，默认只会读取 `infra/.env` 来替换端口变量。

| 服务 | 默认地址（全部只绑定 127.0.0.1） | 变量 |
|---|---|---|
| 前端（Vite，代理 `/api`、`/health` 到后端） | http://localhost:5173 | `OPSPILOT_FRONTEND_PORT` |
| 后端 API / Swagger | http://localhost:8000/docs | `OPSPILOT_BACKEND_PORT` |
| PostgreSQL 17 + pgvector 0.8.7 | 127.0.0.1:5432（库 `opspilot`，测试库 `opspilot_test`） | `OPSPILOT_PG_PORT` |

停止：`docker compose --env-file .env -f infra/docker-compose.yml down`（数据保存在卷 `opspilot_pgdata`；**加 `-v` 会删除数据**）。

### 不用 Docker 的本地进程方式
```bash
scripts/local_pg.sh start                 # PyPI pgserver 便携版 PostgreSQL 16 + pgvector 0.6（无需 sudo）
uv sync && uv run alembic upgrade head && uv run python scripts/seed_demo.py
make dev-backend                          # :8000
cd frontend && npm ci && npm run dev      # :5173，需要 Node 22
```

### 演示账号
`admin@opspilot.local`（管理员）、`alice@opspilot.local`、`bob@opspilot.local`（普通用户）。密码是 `.env` 中的 `OPSPILOT_SEED_ADMIN_PASSWORD` / `OPSPILOT_SEED_USER_PASSWORD`（`.env.example` 里的默认值仅供本地演示）。alice 被授权读取 `order-platform` 知识库，bob 没有（阶段 2 的权限隔离演示用）。

## Mock 模式与真实模型
默认 `OPSPILOT_LLM_PROVIDER=mock`、`OPSPILOT_EMBEDDING_PROVIDER=mock`：离线、确定性、零费用，测试与 CI 都用它。
切换 Qwen：把两项改为 `openai_compatible`，并在 `.env` 中自行填写 `OPSPILOT_LLM_API_KEY`（不要提交）。阶段 1 中没有任何代码路径会调用真实模型。

## 常用命令
| 命令 | 作用 |
|---|---|
| `make test` | 全部 pytest（单元 + 连真实 PostgreSQL 的集成测试，`OPSPILOT_REQUIRE_DB=1` 时数据库不可用即失败） |
| `make test-unit` | 只跑单元测试 |
| `make lint` / `make fmt` | Ruff 检查 / 格式化 |
| `make fe-build` / `make fe-test` | 前端构建 / Vitest |
| `make check` | 以上全部 |

集成测试使用 `OPSPILOT_TEST_DATABASE_URL`（默认 `opspilot_test` 库），每次会 `downgrade base → upgrade head`，**请勿指向有数据的库**。

## 已实现（阶段 1）
- FastAPI 应用工厂、pydantic-settings 配置（带安全校验）、JSON 结构化日志 + request_id + 敏感字段打码、统一错误格式
- Alembic 初始迁移：16 张表（含 `vector(1024)` + HNSW、`tsvector` 生成列 + GIN、审批相关唯一约束）
- `/health/live`、`/health/ready`（DB / pgvector / 迁移 / LLM / Embedding 分项检查，失败 503）
- LLM / Embedding Provider 接口：MockLLM、MockEmbedding（特征哈希）、OpenAI 兼容（Qwen）实现
- JWT 登录、`/me`、`user/admin` 角色依赖，演示数据种子脚本（幂等）
- 最小 React 前端：系统状态 + 登录，连接真实后端
- Dockerfile + Docker Compose：已在 WSL2（Docker 29.9.2 / Compose v5.6.1）实际启动并验证
- GitHub Actions 工作流（尚未在 GitHub 上运行，仓库未推送）

## License
MIT
