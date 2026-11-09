# 运维手册（本地 Compose，阶段 6）

## 环境
- 开发：`docker compose --env-file .env -f infra/docker-compose.yml up -d --build`
- 生产前端：再加 `-f infra/docker-compose.prod.yml --profile prod up -d --build frontend_prod`
- 端口默认只绑 `127.0.0.1`（5173 / 5180 / 8020 / 5433）。
- **禁止**对会 `source .env` 的脚本使用 `bash -x` / `set -x`，避免密钥进入终端记录。

## 健康检查
- `GET /health/live` · `GET /health/ready` · 前端 nginx `/`
- 管理端「可观测性」页或 `GET /api/v1/admin/observability`（admin）· `GET /metrics`（Prometheus 文本）

## 迁移
- 后端容器启动时 `alembic upgrade head`。
- **迁移前**备份主库（示例）：
  ```bash
  mkdir -p data/backups
  docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres \
    pg_dump -U opspilot -Fc opspilot > data/backups/pre_$(date +%Y%m%d%H%M%S).dump
  ```
- 回滚/恢复演示只在一次性库或一次性 volume 上做，不要对主库做破坏性 downgrade。

## 备份与恢复演示（一次性目标）
```bash
# 1) 新建一次性库
docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres \
  psql -U opspilot -c "DROP DATABASE IF EXISTS opspilot_restore_demo; CREATE DATABASE opspilot_restore_demo;"
# 2) 恢复到演示库
docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres \
  pg_restore -U opspilot -d opspilot_restore_demo --clean --if-exists < data/backups/YOUR.dump
# 3) 验证后删除演示库
docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres \
  psql -U opspilot -c "DROP DATABASE opspilot_restore_demo;"
```

## 优雅关闭
- `docker compose stop backend`：uvicorn 停止接收新连接；后台 Agent 线程为 daemon，进行中的运行依赖 PostgresSaver 检查点，重启后可 `resume`。
- 不要 `kill -9` postgres。

## 密钥与付费
- 密钥只存在于本地 `.env`，设置页只显示 `api_key_configured`。
- 付费接口默认关闭；真实 Qwen 评测见 `docs/STAGE6_QWEN_COMPARE_PLAN.md`，**未经批准不得执行**。
