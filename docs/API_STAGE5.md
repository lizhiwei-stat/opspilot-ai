# 阶段 5 API 补充（Web 平台）

Base URL（开发）：`http://localhost:8020`，前端经 Vite / nginx 以 `/api` 反向代理访问。认证、错误格式见 [API.md](API.md)。
角色：`admin` 系统管理员 · `support` 技术支持工程师 · `user` 普通业务用户。下表「staff」= admin 或 support。

## Agent 运行与 SSE
| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/api/v1/agent/runs` | 登录 | `{message, thread_id?, kb_ids?, stream?}`。`stream=true` 时立即返回 `{run_id, thread_id, status:"running"}`，图在后台线程执行，进度从 `/events` 读取；同一会话存在可恢复的中断运行时返回 409 |
| GET | `/api/v1/agent/runs/{id}/events` | 创建者 / admin | **SSE**，见下 |
| POST | `/api/v1/agent/runs/{id}/cancel` | 创建者 / admin | 协作式取消：在下一个节点边界停止，状态 `aborted`，`error="cancelled_by_user"` |
| POST | `/api/v1/agent/runs/{id}/approval?stream=` | admin | 批准 / 驳回 / 退回修改，可后台继续执行 |
| POST | `/api/v1/agent/runs/{id}/resume?stream=` | 创建者 / admin | 从 PostgresSaver 检查点继续 |
| GET | `/api/v1/agent/threads` | 登录 | 本人会话列表（标题、最新状态、运行数） |
| GET | `/api/v1/agent/threads/{thread_id}` | 本人 / admin | 会话下全部运行（刷新后恢复对话） |

### SSE 协议（`text/event-stream`）
- 事件持久化在 `agent_events(run_id, seq, type, data)`，`seq` 每个运行内从 1 递增，唯一。
- 游标：请求头 `Last-Event-ID: <seq>` 或查询参数 `?after=<seq>`，服务端只发送 `seq` 更大的事件（断线重连不丢、不重）。
- 每条事件：`id: <seq>`、`event: <type>`、`data: <JSON>`。首行发送 `retry: 2000`；每 10 秒发送注释心跳 `: hb`。
- 事件类型：`run_started` · `node`（节点完成：name、耗时、摘要）· `tool`（MCP 工具调用结果）· `evidence`（证据条数、得分）· `interrupt`（等待审批）· `cancel_requested` · `final`（最终状态）。
- 结束：运行进入非活动状态且已发出 `final` 后发送 `event: end`；超过 `max_s` 发送 `event: timeout`，客户端可带游标重连。
- 这是**节点 / 事件级流式**，不是逐 token 流式（Mock 模型无 token 流）。
- 前端客户端（`frontend/src/lib/sse.ts`）用 fetch 流以便携带 `Authorization` 头；指数退避重连，最多 8 次，按 `seq` 去重。E2E 中人为在第 2 条事件后切断连接，实测重连请求 `Last-Event-ID` 依次为 `0`、`2`，19 条事件无缺无重。

## 仪表盘
`GET /api/v1/dashboard/summary`（登录；运行类指标 admin 统计全部、其他角色只统计本人；工单指标 staff 全部、普通用户本人）→ 工单总数 / 待处理 / 已完成、Agent 运行数、任务成功率（同时返回 `numerator` / `denominator`，无数据时为 `null` 而不是 0%）、近 14 天每日运行数、延迟 p50 / p95（`percentile_cont`）、工具调用成功率、Mock / 真实模型使用次数、最近运行。全部来自数据库实时查询，无写死数据。

## 工单
| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/v1/mcp/tickets?q&status&priority` | 登录 | user 只看本人；staff 看全部 |
| GET | `/api/v1/mcp/tickets/{number}` | 同上 | 增加 description、resolution、updated_at、事件的 actor_name / approver_name |
| PATCH | `/api/v1/tickets/{number}` | staff | `{status, note, resolution?}`；note 必填，`resolved` 必须填 resolution；非法流转 409。写入 TicketEvent（note 前缀 `[manual:<role>]`） |
| GET | `/api/v1/ticket-transitions` | 登录 | 合法流转表：open→in_progress/resolved/closed；in_progress→open/resolved/closed；resolved→in_progress/closed；closed→in_progress |
| GET | `/api/v1/ticket-drafts` | admin 全部 / 其他人本人 | 草稿 + 审批记录 |

## 知识库管理
| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/api/v1/knowledge-bases` | staff | `{slug, name, description?, visibility}`，创建者为 owner |
| GET | `/api/v1/knowledge-bases/{id}/permissions` | owner / KB admin 授权 / 系统管理员 | 授权列表 |
| PUT | `/api/v1/knowledge-bases/{id}/permissions` | 同上 | 整体替换 `[{user_id, permission: read|write|admin}]`，写入 admin_audit |

## 用户与权限（admin）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/users` · `/admin/roles` · `/admin/audit` | 列表 / 角色说明 / 审计 |
| POST | `/api/v1/admin/users` | 密码至少 10 位且含字母和数字；邮箱重复 409 |
| PATCH | `/api/v1/admin/users/{id}` | 改角色 / 启停 / 重置密码。最后一个启用的管理员不能被降级或停用（行锁 `FOR UPDATE`，409）。审计中密码只记为 `reset` |
| GET | `/api/v1/users/directory` | staff；授权编辑器用的精简用户列表 |
| GET | `/api/v1/admin/settings` | 模型、运行时、付费预算与账本、健康检查。密钥只返回 `api_key_configured: true/false`，任何接口都不返回明文 |

## 评测结果
`GET /api/v1/evals/results`：读取 `evals/results/*.json`，归一为 `rag` / `agent` 两类行，带 `is_mock`、数据集、模型；缺失指标返回 `null`；无法解析的文件列在 `skipped`。前端按 Mock / 真实、数据集分组展示，不同数据集不合并平均。
