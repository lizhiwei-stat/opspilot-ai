# 阶段进度

| 阶段 | 范围 | 状态 | 报告 | Git tag |
|---|---|---|---|---|
| 1 | 需求 / 架构 / 数据库与 API 设计、仓库初始化、后端基础、健康检查、Mock 模式、最小前端、JWT | ✅ 完成（2026-11-02） | [STAGE1_REPORT](reports/STAGE1_REPORT.md) | `stage-1` |
| 2 | 文档上传解析分块、Embedding、基础/混合检索 + RRF、引用、ACL、版本 | ⏳ 未开始 | — | — |
| 3 | LangGraph StateGraph Agent、工具调用、超时/重试/降级 | ⏳ 未开始 | — | — |
| 4 | MCP Server、工单草稿与审批、Checkpointer 恢复 | ⏳ 未开始 | — | — |
| 5 | 完整 Web 管理界面 | ⏳ 未开始 | — | — |
| 6 | 评测体系、安全测试、CI、Docker 集成、文档 | ⏳ 未开始（CI/Compose 骨架已在阶段 1 提供，未在真实 Docker/GitHub 上运行） | — | — |

## 已知待办（跨阶段，非"已完成"）
- 在 WSL2 + Docker 中实际运行 `infra/docker-compose.yml`（阶段 1 沙箱无 Docker）。
- GitHub Actions 尚未在 GitHub 上运行过（仓库未推送）。
- 阶段 1 实测数据库为 PostgreSQL 16.2 + pgvector 0.6.2；Compose/CI 使用 PG17 + pgvector 0.8.7，阶段 6 需在该组合上复测。
