# 阶段进度

| 阶段 | 范围 | 状态 | 报告 | Git tag |
|---|---|---|---|---|
| 1 | 需求 / 架构 / 数据库与 API 设计、仓库初始化、后端基础、健康检查、Mock 模式、最小前端、JWT | ✅ 完成（2026-11-02） | [STAGE1_REPORT](reports/STAGE1_REPORT.md) | `stage-1`（初版，沙箱验证） |
| 1.1 | WSL2 + Docker Compose 实测交付、代码审查修复 | ✅ 已验收 | 同上（§WSL2 实测） | `stage-1.1` |
| 1.2 | 收尾：README 端口说明、`/health/ready` 总超时、真实浏览器登录 E2E | ✅ 完成（2026-11-02） | 同上（§8） | `stage-1.2` |
| 2 | 企业知识库 RAG：上传解析分块、版本与去重、向量/关键词/RRF/重排、ACL 预过滤、带引用问答、注入防护、评测、前端页面、Notebook | ✅ 完成（2026-11-02），等待验收 | [STAGE2_DESIGN](STAGE2_DESIGN.md) · [STAGE2_REPORT](reports/STAGE2_REPORT.md) · [STAGE2_EVAL](reports/STAGE2_EVAL.md) | `stage-2` |
| 2.5 | 真实免费本地 Embedding（bge-small-zh / bge-m3，CPU）、多模型隔离索引、真实 cross-encoder、held-out 冻结评测、拒答/重排/iterative scan/token/注入修复、统一模型调用层与付费守卫、Stage 3 设计、付费对照方案（未执行） | ✅ 完成（2026-11-02），等待验收 | [STAGE2_5_REPORT](reports/STAGE2_5_REPORT.md) · [STAGE2_5_EVAL](reports/STAGE2_5_EVAL.md) · [付费方案](STAGE2_5_PAID_EMBED_PLAN.md) · [STAGE3_DESIGN](STAGE3_DESIGN.md) | `stage-2.5` |
| 3 | LangGraph StateGraph Agent（15 节点）、5 个受控工具、超时/重试/降级、证据评估与拒答、HITL 审批（interrupt）、PostgresSaver 持久化与恢复、Agent 工作台、41 场景 Mock 评测、401 根因修复 | ✅ 完成（2026-11-02，Mock），等待验收；真实 Qwen 测试待授权 | [STAGE3_REPORT](reports/STAGE3_REPORT.md) · [Qwen 方案](STAGE3_QWEN_TEST_PLAN.md) · [ADR-003](adr/ADR-003-stage3-agent.md) | `stage-3` |
| 4 | 三个真实 MCP Server（官方 SDK mcp 2.3，stdio + 可选加固 HTTP）、LangGraph ↔ MCP 集成、ORD-5003 事件 → 草稿 → 审批（批准 / 驳回 / 退回修改 / 过期 / 重启恢复）→ Ticket MCP 建单、调用者令牌与工具安全、审计表（迁移 0005）、前端审批队列、Edge E2E、Notebook | ✅ 完成（2026-11-02，Mock），等待验收；真实 Qwen 测试待授权 | [STAGE4_REPORT](reports/STAGE4_REPORT.md) · [ADR-004](adr/ADR-004-stage4-mcp.md) · [预算方案](STAGE4_QWEN_BUDGET_PLAN.md) | `stage-4` |
| 5 | 企业级 Web 平台：History 路由 SaaS 外壳、三角色权限、仪表盘（真实数据）、SSE 智能体工作台（断线续传、取消、恢复）、知识库与授权管理、工单中心与审批、执行可视化、评测与监控、用户管理（最后管理员保护）、系统设置、生产 nginx 构建、Edge E2E（开发 + 生产） | ✅ 完成（2026-11-08，Mock），等待验收 | [STAGE5_REPORT](reports/STAGE5_REPORT.md) · [页面地图](PAGE_MAP.md) · [API](API_STAGE5.md) · [演示脚本](DEMO_SCRIPT.md) | `stage-5` |
| 6 | 生产工程：架构审查（无 K8s/Redis）、Agent 评测≥100（冻结集）、轻量可观测、Mock 负载、安全矩阵、本地 CI、备份恢复、KB 删除与滚动修复 | ✅ 完成（2026-11-09，Mock），等待验收；真实 Qwen 对照待授权 | [STAGE6_REPORT](reports/STAGE6_REPORT.md) · [LOAD](reports/STAGE6_LOAD.md) · [SECURITY](reports/STAGE6_SECURITY.md) · [Qwen 计划](STAGE6_QWEN_COMPARE_PLAN.md) · [ADR-005](adr/ADR-005-stage6-engineering.md) | `stage-6` |

## 验收后可选
真实 Qwen 对照（[计划](STAGE6_QWEN_COMPARE_PLAN.md)）· 远端 CI push · 合成评测期望校准。
