# ADR-005：阶段 6 工程化选型（轻量可观测、不引入 K8s/微服务）

## 现状审查摘要
| 子系统 | 结论 |
|---|---|
| FastAPI | 统一错误模型、JWT、角色依赖齐全；阶段 5/6 补齐平台与观测接口 |
| LangGraph | 单图 + PostgresSaver，协作式取消与审批 interrupt 已验证 |
| MCP | stdio 子进程 + 调用者令牌；审计表已有 |
| PG/pgvector | 单库足够；HNSW + 全文；连接池由 SQLAlchemy + psycopg pool 管理 |
| React | History 路由 SaaS 壳；无需 SSR |
| Compose | 开发 / 生产前端分离；端口本机回环 |
| 费用控制 | ModelGateway 多重守卫；默认 Mock |
| 认证审批 | 三角色 + 最后管理员保护 + 审批不可自批 |

## 决策
1. **不引入 Kubernetes / 微服务拆分**：单机 Compose 已覆盖交付；拆分只会增加网络与发布复杂度。
2. **不引入 Redis**：会话、事件、检查点已在 Postgres；Redis 多一个故障点与备份面。
3. **不引入完整 OTel + Prometheus + Grafana 三件套**：维护成本高。采用 **structlog JSON + trace_id/request_id + 进程内 span 环形缓冲 + 可选 `/metrics` 文本**；持久轨迹仍在既有表。若未来多机部署，再迁 OTel。
4. **评测**：Mock 自动化 ≥100 场景，dev/validation/final_test 冻结哈希；真实 Qwen 对照单独计划与预算。

## 后果
- 进程重启会丢失内存 span（文档已说明）；DB 审计仍在。
- `/metrics` 无鉴权但只暴露计数器，不含密钥；生产若暴露到非本机，应加网络限制（当前绑 127.0.0.1）。
