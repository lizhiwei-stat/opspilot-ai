# 页面地图（阶段 5）

History 路由（`frontend/src/lib/router.tsx`），旧的 `#hash` 地址自动跳转。未登录访问任何页面跳到 `/login?next=…`；无权限显示「无权访问」页（对应 API 返回 403）。

| 路由 | 页面 | 角色 | 主要接口 | 截图 |
|---|---|---|---|---|
| `/login` | 登录（表单校验、错误提示、会话过期提示） | 公开 | `POST /auth/login` | [s5_login](assets/stage5/s5_login.png) |
| `/` | 仪表盘：工单、运行、成功率（含分子分母）、p50/p95、工具成功率、Mock/真实用量、每日趋势、最近运行 | 全部（按角色限定范围） | `/dashboard/summary` | [s5_dashboard_after](assets/stage5/s5_dashboard_after.png) |
| `/workspace[/:threadId]` | 智能体工作台：会话列表、SSE 实时节点 / 工具 / 证据面板、取消、恢复、审批状态 | 全部 | `/agent/runs`、`/events`、`/threads` | [s5_workspace_pending](assets/stage5/s5_workspace_pending.png) |
| `/knowledge?tab=docs|search|qa|manage` | 知识库：文档与分块、上传 / 更新版本 / 删除（确认框）、检索测试、问答、知识库与授权管理 | 全部；管理 tab 需 staff | `/knowledge-bases`、`/documents`、`/search`、`/qa`、`/permissions` | [s5_knowledge_docs](assets/stage5/s5_knowledge_docs.png) · [s5_kb_permissions](assets/stage5/s5_kb_permissions.png) |
| `/tickets[/:number]?tab=list|drafts` | 工单中心：筛选、详情与事件时间线、人工流转（staff）、草稿审批（admin，批准 / 驳回确认 / 退回修改） | 全部 | `/mcp/tickets`、`PATCH /tickets`、`/ticket-drafts`、`/approval` | [s5_tickets_list](assets/stage5/s5_tickets_list.png) · [s5_ticket_detail](assets/stage5/s5_ticket_detail.png) · [s5_drafts_pending](assets/stage5/s5_drafts_pending.png) |
| `/executions[/:runId]` | 执行可视化：流水线图（节点状态、耗时）、工具调用、证据、事件日志 | 全部（本人 / admin 全部） | `/agent/runs`、`/history`、`/events` | [s5_executions](assets/stage5/s5_executions.png) |
| `/evals?tab=…` | 评测与监控：Mock / 真实分开，按数据集分组 | 全部 | `/evals/results`、`/evals/runs` | [s5_evals](assets/stage5/s5_evals.png) |
| `/admin/users` | 用户与权限：建用户、改角色、启停、重置密码、审计 | admin | `/admin/users`、`/admin/audit` | [s5_admin_users](assets/stage5/s5_admin_users.png) |
| `/admin/settings` | 系统设置（只读）：模型、运行时、付费开关与预算、健康检查；密钥只显示是否配置 | admin | `/admin/settings` | [s5_settings](assets/stage5/s5_settings.png) |
| `/lab/agent` | Agent 调试台（阶段 3/4 原工作台，保留给回归 E2E） | 全部 | 同阶段 4 | — |

生产构建（nginx，5180）的同组截图见 `assets/stage5/prod/`。
