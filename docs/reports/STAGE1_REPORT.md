# 第一阶段开发报告（Stage 1 / 1.1）

- 2026-11-02 上午（Asia/Hong_Kong）
- `stage-1`（d06eabc）：初版，在开发沙箱中验证，保留不动。
- `stage-1.1`：在 Teddy 的 WSL2 上用 Docker Compose 实际部署，加上代码审查后的修复。本报告以 stage-1.1 为准。

验证范围分三类：
- **已实际验证**：命令真的执行过，下面贴的是原始输出。
- **仅静态检查**：做过语法或配置检查，没有真正运行。
- **尚未验证**：既没运行，也没检查。

---

## 1. 环境（WSL2 实测）

| 项 | 版本 / 结果 |
|---|---|
| WSL | Ubuntu-24.04（WSL2） |
| Git | 2.43.0 |
| Docker / Compose | Docker 29.9.2（build a82e528）/ Docker Compose v5.6.1 |
| uv | 0.13.4（宿主）；镜像内为 0.13.5 |
| Python | 宿主 `.venv`：3.11.17（uv 自动下载）；后端镜像：3.11.18（`python:3.11-slim`） |
| Node | 前端镜像 `node:22-alpine`：v22.23.4；宿主 WSL 没有安装 Node（不需要） |
| PostgreSQL / pgvector | PostgreSQL 17.11（Debian 17.11-1.pgdg13+2）/ vector 0.8.7 |
| 端口（Teddy 机器上的取值） | PG `127.0.0.1:5433`，后端 `127.0.0.1:8020`，前端 `127.0.0.1:5173` |

端口说明：8000 和 8010 已被 zhanggui 项目占用（8000 是 zhanggui-api 容器，8010 是 `offline_demo.py`），所以本机 `.env` 改用 8020 和 5433。zhanggui 的容器和进程都没有动过。

本次没有执行任何 sudo 或系统级改动，也没有调用付费模型（全程 Mock 模式）。

## 2. 已实际验证（WSL2，原始输出节选）

### 2.1 Docker Compose 启动
```
$ docker compose --env-file .env -f infra/docker-compose.yml up -d --build
 Image opspilot-backend Built
 Image opspilot-frontend Built
 Container opspilot-postgres-1 Started
 Container opspilot-postgres-1 Healthy
 Container opspilot-backend-1 Started
 Container opspilot-backend-1 Healthy
 Container opspilot-frontend-1 Started
$ docker ps --filter name=opspilot
opspilot-backend-1 Up 27 seconds (healthy) 127.0.0.1:8020->8000/tcp
opspilot-frontend-1 Up 3 minutes 127.0.0.1:5173->5173/tcp
opspilot-postgres-1 Up 6 seconds (healthy) 127.0.0.1:5433->5432/tcp
```
第一次启动失败：后端容器以 `exited (2)` 退出，日志为 `Failed to initialize cache at /home/app/.cache/uv: Permission denied`。原因和修复见 §4。

### 2.2 PostgreSQL 与 pgvector
```
PostgreSQL 17.11 (Debian 17.11-1.pgdg13+2) on x86_64-pc-linux-gnu
 vector | 0.8.7 | public | vector data type and ivfflat and hnsw access methods
public 表数量: 16
```

### 2.3 Alembic 迁移
后端容器启动时会自动执行 `alembic upgrade head`：
```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
$ docker exec opspilot-backend-1 alembic current   ->  0001 (head)
$ docker exec opspilot-backend-1 alembic check     ->  No new upgrade operations detected.
$ (宿主 uv) alembic current / check（连 compose 库 :5433）  ->  0001 (head) / No new upgrade operations detected.
```
回滚往返测试只在一次性库 `opspilot_rt` 上做，做完就删掉了：
```
$ alembic upgrade head   ; current -> 0001 (head)
$ alembic downgrade base ; current -> (空)
$ alembic upgrade head   ; current -> 0001 (head)
$ alembic check -> No new upgrade operations detected.   表数量 16
DROP DATABASE opspilot_rt  -> 剩余库: opspilot, opspilot_test, postgres, template0, template1
```

### 2.4 种子数据
```
$ docker exec opspilot-backend-1 python scripts/seed_demo.py
seed complete, newly created: {'users': 3, 'knowledge_bases': 2, 'kb_permissions': 1}
$ （再次执行）
seed complete, newly created: {'users': 0, 'knowledge_bases': 0, 'kb_permissions': 0}
```

### 2.5 健康检查
```
$ curl -s localhost:8020/health/live
{"status":"alive","version":"0.1.0"}
$ curl -s localhost:8020/health/ready
{"status":"ready","checks":[{"name":"database","ok":true,"latency_ms":10.62,"detail":"postgres 17.11 (Debian 17.11-1.pgdg13+2)"},{"name":"pgvector","ok":true,"latency_ms":1.61,"detail":"pgvector 0.8.7, cosine_distance_probe=1.0"},{"name":"migrations","ok":true,"latency_ms":0.81,"detail":"alembic revision 0001"},{"name":"llm","ok":true,"latency_ms":0.04,"detail":"mock/mock-llm-v1: offline deterministic mock"},{"name":"embedding","ok":true,"latency_ms":0.2,"detail":"mock/mock-hash-embedding-v1 dim=1024"}]}
HTTP 200
```

### 2.6 数据库不可用时返回 503，恢复后回到 200
```
$ docker compose ... stop postgres
$ curl -s localhost:8020/health/ready
{"status":"not_ready","checks":[{"name":"database","ok":false,"latency_ms":8002.91,"detail":"OperationalError: (psycopg.OperationalError) failed to resolve host 'postgres': [Errno -3] Temporary failure in name resolution"},{"name":"pgvector","ok":false,"latency_ms":0.0,"detail":"skipped: database unavailable"},{"name":"migrations","ok":false,"latency_ms":0.0,"detail":"skipped: database unavailable"},{"name":"llm","ok":true,...},{"name":"embedding","ok":true,...}]}
HTTP 503
$ curl localhost:8020/health/live  -> {"status":"alive","version":"0.1.0"} HTTP 200
$ docker compose ... start postgres ; sleep 6 ; curl .../health/ready  -> HTTP 200
```
修复前，这次请求要等约 24 秒（三项检查各等 8 秒）。修复后只等第一次 DNS 失败的约 8 秒，见 §4。

### 2.7 前端可访问，并通过代理连接后端
```
$ curl -s localhost:5173/ | grep -o '<title>.*</title>'   -> <title>OpsPilot AI</title>
$ curl -s localhost:5173/health/ready                     -> {"status":"ready",...}  HTTP 200
$ curl -s localhost:5173/api/v1/system/version
{"name":"OpsPilot AI","version":"0.1.0","stage":1,"env":"dev","llm_provider":"mock",...,"embedding_dim":1024}
$ CORS 预检 OPTIONS :8020/api/v1/auth/login  Origin: http://localhost:5173
HTTP/1.1 200 OK
access-control-allow-origin: http://localhost:5173
```
截图 `docs/assets/stage1-wsl-compose.png`：用 Windows 上的 Edge 无头模式访问 `http://localhost:5173`。页面上的 PG 17.11 和 pgvector 0.8.7 来自后端真实接口返回，没有写死在页面里。

### 2.8 JWT 登录与权限（经前端代理）
```
admin token len 308, user token len 307
GET /api/v1/auth/me (admin) -> {"id":"43f45bf0-…","email":"admin@opspilot.local","display_name":"Ops Admin","role":"admin"}
GET /api/v1/auth/me (alice) -> {"id":"f2a7407c-…","email":"alice@opspilot.local","display_name":"Alice (Support)","role":"user"}
GET /api/v1/system/admin-ping (admin) -> {"status":"ok","scope":"admin"} HTTP 200
GET /api/v1/system/admin-ping (alice) -> {"error":{"code":"forbidden","message":"Requires role: admin",...}} HTTP 403
GET /api/v1/auth/me (无 token)        -> {"error":{"code":"unauthorized","message":"Missing bearer token",...}} HTTP 401
POST /api/v1/auth/login 错误密码       -> {"error":{"code":"unauthorized","message":"Invalid email or password",...}}
```

### 2.9 数据持久化
```
INSERT INTO audit_logs(action,...) VALUES ('persistence_marker',...)   users=3, marker=1
$ docker compose ... restart        -> users=3, marker=1, ready HTTP 200
$ docker compose ... down（不加 -v） -> 卷 opspilot_pgdata 仍在
$ docker compose ... up -d          -> users=3, marker=1, ready HTTP 200
```

### 2.10 测试与静态检查（WSL 宿主，连 compose 中的 `opspilot_test`）
```
$ OPSPILOT_REQUIRE_DB=1 uv run pytest -v   ->  38 passed in 1.53s   （0 failed，0 skipped）
$ uv run ruff check .                      ->  All checks passed!
$ uv run ruff format --check .             ->  52 files already formatted
$ docker exec opspilot-frontend-1 npm test ->  Test Files 1 passed (1) / Tests 2 passed (2)
$ docker exec opspilot-frontend-1 npm run build -> ✓ built（index.js 224.53 kB）
```
38 个 pytest 用例中，16 个是单元测试，22 个是连真实 PostgreSQL 17 + pgvector 0.8.7 的集成测试。开发沙箱（PostgreSQL 16.2 + pgvector 0.6.2）上同样是 38 passed。

## 3. 仅静态检查 / 尚未验证

| 项 | 状态 | 说明 |
|---|---|---|
| `.github/workflows/ci.yml` | 仅静态检查 | 只做了 YAML 解析；仓库没有推送，没有在 GitHub 上运行过 |
| 浏览器里点击登录 | 尚未验证 | 登录接口已经通过前端代理用 curl 验证；页面上的登录表单没有用浏览器实际点过（Vitest 覆盖了页面渲染和错误展示） |
| `scripts/local_pg.sh` 在 WSL 上的运行 | 尚未验证 | 只在开发沙箱实测过；WSL 上走的是 Docker |
| 生产构建方式（nginx 静态托管前端） | 尚未实现 | 前端容器现在用的是 Vite dev server，适合本地演示，不适合生产环境 |
| OpenAI 兼容（Qwen）真实调用 | 尚未验证（有意为之） | 阶段 1 不调用付费 API；单元测试只验证了错误包装 |

## 4. 代码审查：发现的问题与修复

| # | 问题 | 影响 | 修复 | 复测 |
|---|---|---|---|---|
| 1 | 后端镜像里用 `uv run` 启动，非 root 用户没有 uv 缓存目录 | 容器启动失败，exit 2 | 把 `.venv/bin` 加入 PATH，直接执行 `alembic` 和 `uvicorn` | compose up 后为 healthy |
| 2 | 端口写死且绑定 0.0.0.0；数据库账号密码写死在 compose 里 | 和 zhanggui 端口冲突，并且暴露到局域网 | 所有端口只绑 127.0.0.1，并可用 `OPSPILOT_*_PORT` 配置；数据库账号密码改为 `OPSPILOT_PG_*` 变量（默认 opspilot，仅限本地）；CORS 跟随前端端口 | 5433/8020/5173 实测 |
| 3 | compose 文件在 `infra/` 下，默认只读 `infra/.env` | 根目录 `.env` 里的端口变量不生效 | 命令统一加 `--env-file .env`，README 和 Makefile 同步修改 | 实测 |
| 4 | 集成测试会先 downgrade 到 base（清空所有表），但没有检查连的是哪个库 | 测试 URL 填错时可能清空主库 | 库名不以 `_test` 或 `_rt` 结尾时直接报错，拒绝执行 | 38 passed |
| 5 | 测试只从进程环境变量读测试库地址，不读 `.env` | WSL 上 17 个测试报错（连到了 5432） | 用 python-dotenv 读取 `.env`（已作为显式 dev 依赖） | 38 passed |
| 6 | `.env.example` 里的 JSON 值没有加引号，`source .env` 后引号被 shell 去掉 | 宿主上 alembic 启动即崩溃（cors_origins 解析失败） | 值改为单引号包裹 | 宿主 alembic current/check 通过 |
| 7 | 数据库宕机时 ready 接口要等三次连接超时 | 探针约 24 秒才返回 | 数据库检查失败时，跳过依赖它的两项检查 | 约 8 秒返回 503 |
| 8 | `env.py` 没有调用 `fileConfig`，迁移日志不输出 | 看不到迁移过程 | 补上 `fileConfig`，并设置 `propagate=0` 避免日志重复 | 容器日志可见 |
| 9 | prod 环境下没有拒绝 `.env.example` 里的 JWT 占位值 | 可能带着默认密钥上线 | 占位值以 `change-me` 开头时拒绝启动，并补了测试 | 通过 |
| 10 | 测试有一部分只断言状态码 | 验证力度不够 | 新增测试：token 的 sub/role 与用户一致；停用用户的 token 和登录都会被拒；种子幂等且 ACL 记录准确（只有 alice 能读 order-platform），密码为 argon2id 存储；数据库不可用时 503 且失败项准确；500 错误不泄露异常细节 | 32 → 38 |
| 11 | 没有 `.dockerignore`；`tsconfig.tsbuildinfo` 被提交进了仓库 | 构建上下文偏大，可能把 `.env` 或 node_modules 带进镜像 | 新增 `.dockerignore`；从 git 移除 tsbuildinfo 并加入 ignore | 已重新构建 |
| 12 | 本地 Python 版本没有固定（WSL 默认是 3.12） | 本地和镜像（3.11）版本不一致 | 新增 `.python-version`，值为 3.11 | 宿主 3.11.17 |

**审查后确认没有问题的部分**
- 分层：API 路由只调用 Service，Service 调用 Repository；配置只在 `core/config.py` 中读取。
- 健康检查服务直接执行 SQL，这是探针性质的代码，可以接受。
- 前端只调用真实接口（`/health/ready`、`/api/v1/system/version`、`/auth/login`、`/auth/me`），没有写死数据。只有 Vitest 里 mock 了 fetch，这是测试本身的需要。
- `.env` 已经在 `.gitignore` 中（`git check-ignore -v .env` 输出 `.gitignore:1:.env`），没有被提交。
- 仓库中没有 `sk-` 开头的密钥，也没有 DashScope Key。

**有意保留的明文默认值**
- `.env.example` 中的 `OPSPILOT_SEED_*` 是本地演示账号的密码，`OPSPILOT_PG_PASSWORD=opspilot` 是本地开发用的数据库密码。
- 端口只绑在 127.0.0.1 上，外部访问不到；README 已经写明这些值仅用于本地演示。

**版本一致性**
- Python 3.11：pyproject、`.python-version`、Dockerfile、CI 一致。
- uv：镜像 0.13.5，CI 0.13.5，宿主 0.13.4。lock 文件可以兼容，这一点已经实测（`uv sync --frozen` 通过）。
- Node 22：package.json 的 `engines >=22.22.2`、镜像 22.23.4、CI 22 一致。
- pgvector：compose 和 CI 用 0.8.7（本次实测），开发沙箱是 0.6.2（同样 38 passed）。代码只用到 HNSW 和余弦距离，这两者从 0.5 版本起就支持。

## 5. 从零启动命令（WSL2）
见 README「快速开始」。在 Teddy 机器上实际使用的版本如下：
```bash
cd ~/projects/opspilot-ai
cp .env.example .env
sed -i 's/^OPSPILOT_PG_PORT=.*/OPSPILOT_PG_PORT=5433/; s/^OPSPILOT_BACKEND_PORT=.*/OPSPILOT_BACKEND_PORT=8020/; s#127.0.0.1:5432/#127.0.0.1:5433/#g' .env
sed -i "s/^OPSPILOT_JWT_SECRET=.*/OPSPILOT_JWT_SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')/" .env
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
docker compose --env-file .env -f infra/docker-compose.yml exec backend python scripts/seed_demo.py
curl -s localhost:8020/health/ready
uv sync --frozen && OPSPILOT_REQUIRE_DB=1 uv run pytest -v && uv run ruff check .
docker compose --env-file .env -f infra/docker-compose.yml exec frontend npm test
# 浏览器: http://localhost:5173  账号 admin@ / alice@ / bob@opspilot.local，密码见 .env 的 OPSPILOT_SEED_*
```

## 6. 已实现 / 未实现
**已实现**
- 后端基础：配置、结构化日志与 request_id、统一错误格式。
- 完整的数据库 schema 和迁移。
- live / ready 健康检查。
- Mock 与 OpenAI 兼容两套 Provider。
- JWT 认证和角色控制。
- 种子数据。
- 最小前端。
- Docker Compose 部署。

**未实现**
- 阶段 2 到 6 的全部功能：RAG、Agent、MCP、审批、完整界面、评测、安全测试。
- LangChain、LangGraph、MCP SDK 尚未加入依赖。

## 7. 第二阶段开始前仍需解决的问题
1. **CI 没有真正运行过**：需要推送到 GitHub（必须由 Teddy 同意）后，确认 Actions 能通过。
2. **数据库宕机时 ready 仍要约 8 秒**：耗时在 Docker DNS 解析失败，`connect_timeout=3` 管不到 DNS 这一步。可以考虑给探针加整体超时（例如在线程中执行并限制 2 秒），或者接受现状，并把容器探针超时设为 10 秒以上。
3. **前端容器是 Vite dev 模式**：阶段 5 或 6 改为构建产物 + nginx，同时决定由 nginx 反向代理 API，还是靠 CORS 直连后端。
4. **中文关键词检索**：阶段 2 需要选定中文分词器（如 jieba），并确定 `search_text` 的生成规则。
5. **LangGraph checkpoint 表**：由 `langgraph-checkpoint-postgres` 的 `setup()` 自行管理，需要在阶段 4 确认它和 Alembic 共存的方式。
6. **工具版本差异**：宿主 uv 0.13.4 与镜像的 0.13.5 有小版本差异，可以统一升级。
7. **演示账号密码**：现在是公开在 `.env.example` 里的默认值，正式演示前建议在 `.env` 中改掉。
