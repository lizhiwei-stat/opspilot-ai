# 阶段 4 报告：MCP 工具接入 + LangGraph 审批工作流

日期：2026-11-02（HKT）· 版本 v0.4.0 · tag `stage-4`（box 与 WSL 同一提交）
范围：三个真实 MCP Server（官方 SDK，stdio；可选加固的 Streamable HTTP）、LangGraph ↔ MCP 集成、ORD-5003 事件 → 草稿 → 人工审批 → 业务操作、退回修改、过期、重启恢复、工具安全、审计、前端审批界面。
**全部使用 Mock 模型，本阶段没有任何付费调用**（包括未重跑 Qwen 冒烟）。真实模型方案见 [STAGE4_QWEN_BUDGET_PLAN](../STAGE4_QWEN_BUDGET_PLAN.md)（未执行，待授权）。

## 0. 结论速览
| 项 | 结果 |
|---|---|
| 版本 | mcp **2.3.0**（协议 2026-07-28）、langgraph 1.2.19、langgraph-checkpoint-postgres 3.2.0；**不用** langchain-mcp-adapters（0.3.2 要求 mcp<2）。理由见 [ADR-004](../adr/ADR-004-stage4-mcp.md) |
| MCP Server | `opspilot-knowledge`（3 个工具）、`opspilot-ticket`（4 + 1 个仅后端用）、`opspilot-diagnostics`（3 个），真实 `tools/list` / `tools/call` |
| 后端测试 | box **244 passed, 1 skipped**；WSL **244 passed, 1 skipped**（其中 Stage 4 新增 25 个，阶段 3 的 219 个全部仍通过） |
| 前端 | vitest **16 passed**，`tsc` 与 `vite build` 通过 |
| Agent 评测 | 45 个场景（新增 4 个），经 **mcp-stdio** 传输，**45/45**，未授权执行 0、未审批副作用 0（Mock） |
| 实时演示 | WSL Docker Compose 栈（backend 8020 / frontend 5173 / postgres 5433），容器内 MCP 演示客户端、Windows Edge 浏览器 E2E **5/5 PASS** |
| Notebook | [notebooks/mcp_agent_workflow.ipynb](../../notebooks/mcp_agent_workflow.ipynb)，已执行，含输出，无报错 |

## 1. 已实际验证
### 1.1 MCP 协议：发现与调用（真实子进程）
WSL Compose 后端容器内运行 `python scripts/mcp_demo_client.py`（[日志](stage4_artifacts/mcp_demo_wsl_compose.log) · [JSON](stage4_artifacts/mcp_demo_wsl_compose.json)）：
```
== tools/list ==
  knowledge    protocol=2026-07-28  get_document, list_knowledge_bases, search_knowledge
  ticket       protocol=2026-07-28  create_ticket_draft, get_ticket, list_tickets, revise_ticket_draft, update_ticket_status
  diagnostics  protocol=2026-07-28  analyze_error_code, get_service_status, query_system_logs
== tools/call ==
  knowledge.list_knowledge_bases   ok                     256ms
  knowledge.search_knowledge       ok                     344ms
  diagnostics.analyze_error_code   ok                     397ms
  diagnostics.query_system_logs    ok                       8ms  {"service": "order-service", "total": 3, "by_code": {"ORD-5003": 3}, ...}
  diagnostics.get_service_status   ok                       6ms  {"status": "degraded", "error_rate_5m": 0.6, ...}
  ticket.list_tickets              ok                      26ms
  ticket.get_ticket                ToolNotFound             5ms  ticket TCK-0002 not found      ← alice 看 bob 的工单：服务端 ACL
  diagnostics.query_system_logs    ToolValidationError      0ms  invalid arguments: 1 error(s)  ← service="../../etc"
```
工具输入/输出都是 Pydantic 模型（`backend/src/opspilot/mcp/contracts.py` + 阶段 3 的模型），通过 SDK 发布为 `input_schema` / `output_schema`，客户端再按同一契约校验输出（`structured_content`）。

### 1.2 ORD-5003 端到端（LangGraph + MCP + PostgresSaver）
`tests/mcp/test_incident_flow.py` 与 Notebook §5–§7 的真实输出（box 测试库）：
```
intent: incident_ticket | status: awaiting_approval
trace: guard_in → classify → plan → retrieve → assess → agent → tools → collect → assess → generate → verify → draft
  get_service_status     ok   ServiceStatusOut: {"service": "order-service", "status": "degraded", ...}
  query_system_logs      ok   3 log lines for order-service; by_code={'ORD-5003': 3}
  analyze_error_code     ok   ORD-5003: service=order-service class=5xx retryable=True kb_rows=3
  search_knowledge       ok   5 hits: order-api-error-codes.md, ..., postmortem...
  create_ticket_draft    ok   draft 55be1290-... status=pending_approval        ← draft 节点服务端拼装
alice approve -> ForbiddenError approval requires admin
queue has run: True
（关闭连接池 + 全部 MCP 子进程，新建 Service = 模拟重启）
退回不带意见 -> return-for-revision needs a comment for the author
after return: awaiting_approval | draft v 2 parent 55be1290
after approve: completed | draft: executed
draft_result: {"status": "executed", "decision": "approved", "revision": 2, "ticket_number": "TCK-0004",
               "business_op": {"ok": true, "status": "open", "deduplicated": false, ...}, "approval_id": "...", "reviewer_id": "..."}
trace tail: approval → apply_approval → revise → approval → apply_approval → respond
replay -> {'ticket_number': 'TCK-0004', 'status': 'open', 'deduplicated': True}
TCK-0004 ORD-5003 order-service 排查与处理 P2 open | tickets for this draft: 1
  event None -> open approver=290a4f78 approved_at=18:53:26
```
步骤对应：理解（classify=incident_ticket）→ RAG（retrieve + Knowledge MCP `search_knowledge`）→ 日志（Diagnostics `query_system_logs`）→ 错误码关联（`analyze_error_code` + `get_service_status`）→ 方案（generate + verify 引用核验）→ 草稿（Ticket `create_ticket_draft`）→ 人工审批（`interrupt`）→ 业务操作（Ticket `update_ticket_status`）。
上限：`agent_max_steps=24`、`agent_max_tool_calls=6`、`agent_max_react_rounds=3`、`agent_run_timeout_s=60`、每次工具调用 `agent_tool_timeout_s=5`、`mcp_max_revisions=2`、审批 TTL 24h；退回超过上限 → 结束且不建单（已测试）。

### 1.3 HITL 覆盖（全部为真实 MCP + PostgresSaver 测试）
| 场景 | 测试 | 结果 |
|---|---|---|
| 提交审核 → 批准 → 建单一次 | `test_incident_pipeline_approve_creates_ticket_once` | ✅ 普通用户 403、重复批准 409、1 张工单、事件含审批人 |
| 驳回 | `test_reject_creates_no_ticket` | ✅ 不调用 update_ticket_status |
| 退回修改 → 再批准 | `test_return_for_revision_then_approve`、API 测试 | ✅ 无意见 409；v2 + parent；旧版 `returned`；2 条审批记录；新版建单 |
| 退回次数上限 | `test_revision_limit_ends_without_ticket` | ✅ |
| 审批超时 | `test_expiry_creates_no_ticket` | ✅ `expired` |
| 后端重启恢复待审批 | `test_restart_resumes_pending_approval_with_new_mcp_processes` | ✅ 关闭所有 MCP 子进程后，新 Service 的队列仍能列出，审批后建单 |
| 用户 / 模型写“已批准” | `test_approved_text_from_user_or_forged_resume_is_not_an_approval` | ✅ 文本无效；伪造的 resume（非 admin reviewer）不执行 |
| 业务操作重放 | `test_update_ticket_status_replay_is_deduplicated` | ✅ `deduplicated=True`，仍 1 张 |

### 1.4 工具安全（代码级控制，`tests/mcp/test_protocol.py`，15 个）
| 风险 | 控制 | 证据 |
|---|---|---|
| 越权调用 / 角色伪造 | 服务端按令牌 `sub` 从数据库加载用户与角色；ACL 在 MCP Server 内执行 | `test_acl_is_enforced_inside_the_server`：令牌声明 role=admin 的 alice 仍看不到 TCK-0002 |
| 参数篡改 / 身份注入 | 令牌绑定参数哈希；schema 不含 user_id，多余参数被拒 | `test_raw_protocol_caller_token_is_required_and_bound`：缺失 / 无效 / 篡改 / 错 server / 错 tool / 过期 / 重放 / 多余参数 → 全部 `[permission]` 或参数错误 |
| 重放 / 重复 | jti 重放缓存 + TTL 30s；写操作数据库唯一约束 + 幂等键 | 同上 + §1.3 |
| 白名单 / 无任意 shell、路径、外链 | Registry 只允许 3 个模块（`python -m`，无 shell）；Hub 只接受契约内工具；没有文件路径或 URL 参数的工具；service 等字段有正则 | `test_argument_validation_client_and_server_side`（`../etc/passwd`、`run_shell`、未知 server） |
| 不可信 MCP Server | 校验服务器名；忽略契约外工具与 schema 不符的工具；输出大小上限；输出契约校验 | `test_hostile_server_tools_filtered_and_outputs_rejected`、`test_impostor_server_is_refused`（`tests/mcp/evil_server.py`） |
| 不可信工具输出 / 注入 | 沿用阶段 3：证据清洗 + flagged + 不可信包裹 | `test_prompt_injection_in_kb_is_flagged_not_obeyed`；评测 a31–a33 |
| 超时 / 不可用 / 崩溃 | 每次调用超时；连接超时；崩溃后标记不可用、下次自动重启 | `test_timeout_and_crash_then_relaunch` |
| 敏感数据泄露 | 子进程环境不含 LLM Key、真实 JWT 密钥；审计参数只存摘要 + 哈希，经 redact | `test_child_env_has_no_secrets`、`test_server_audit_rows_are_written_and_redacted` |
| HTTP 传输 | 仅回环地址；Bearer；Origin 白名单；Host 校验；64 KiB | `test_http_transport_refuses_non_loopback_bind`、`test_http_transport_auth_origin_and_call`（无/错 Bearer 401、恶意 Origin 403、错误 Host 被拒、有 Bearer 无令牌仍拒绝） |

### 1.5 审计
- 表 `mcp_tool_audit`（迁移 0005）：side（server/client）、server、tool、transport、user_id、user_role、thread_id、run_id、call_id、jti、args_summary（redact 后）、args_sha256、status（ok/error/denied/invalid/timeout/unavailable/replay）、error_class、error、duration_ms、output_bytes、idempotency_key、created_at。
- 表 `ticket_events`：ticket、draft、approval（UNIQUE）、from→to、actor、approver、approved_at、note。`approvals` 增加 `returned` 决定；`ticket_drafts` 增加 revision / parent_draft_id / target_status / thread_id。
- 迁移 0005：升级、降级（`returned` → `rejected`，保留数据）、再升级已测试；模型与迁移无漂移（`test_schema`）。
- API：`GET /api/v1/mcp/audit?thread_id=`（普通用户只能看自己的）、`GET /api/v1/tickets`、`/tickets/{number}`（含事件）、`GET /api/v1/agent/approvals`（admin）、`GET /api/v1/mcp/servers`（admin，实时 `tools/list`）。

### 1.6 前端与浏览器 E2E
Agent 页新增：待审批队列（admin）、审批意见 + **退回修改**（无意见时禁用）、草稿内容与版本、审批结果与工单号、工单状态流转、MCP 审计表；普通用户看不到审批按钮和队列。
Windows 11 无头 Edge → WSL Compose（[日志](stage4_artifacts/windows_edge_e2e.log)）：登录 ×2、RAG 页面、阶段 3 Agent（已更新为期望 `executed`）、阶段 4 MCP 流程，全部 PASS：
```
{"ok": true, "elapsed_s": 2.7, "checks": {"alice_incident_pipeline": "KB+logs+error code+status via MCP -> draft -> awaiting",
  "return_for_revision": "returned with comment -> revise_ticket_draft -> v2 pending", "ticket": "TCK-0005",
  "approve_business_op": "approved -> Ticket MCP update_ticket_status -> ticket open", "audit_rows_visible": true}}
```
截图：[待审批（用户视角）](../assets/stage4_incident_pending_user.png) · [审批队列](../assets/stage4_approval_queue.png) · [退回后第 2 版](../assets/stage4_returned_v2.png) · [建单 + 审计](../assets/stage4_ticket_created_audit.png)

### 1.7 评测（Mock，经 MCP）
[`evals/results/20261102T105133Z_agent_mock_mcp_box.json`](../../evals/results/20261102T105133Z_agent_mock_mcp_box.json)：`tool_transport=mcp-stdio`，45 场景全部通过；tool_call_success_rate 0.9231（失败的都是注入的故障，排除后 0.9574）；p50 38ms / p95 1869ms（p95 含 MCP 子进程冷启动）；费用 ¥0。a29、a39 的期望从 `approved` 改为 `executed`（审批后执行业务操作）；新增 a42–a45（incident 批准 / 驳回 / 重放 / 非管理员审批）。

## 2. 仅静态检查
- ruff check / ruff format --check：通过（box）。
- 架构图与数据流图：[stage4_mcp_architecture.png](../diagrams/stage4_mcp_architecture.png)（[mmd](../diagrams/stage4_mcp_architecture.mmd)）、[stage4_mcp_dataflow.png](../diagrams/stage4_mcp_dataflow.png)（[mmd](../diagrams/stage4_mcp_dataflow.mmd)），由 mermaid-cli 11 渲染。图是根据代码手画的，没有和运行时自动对照。
- 多 uvicorn worker 下每个 worker 各起 3 个 MCP 子进程：只读代码推断，没有压测。

## 3. 尚未验证 / 未完成
1. **真实大模型**：没有跑。真实 Qwen 下 incident 路径的工具选择、方案质量与草稿质量都未知；方案见 [STAGE4_QWEN_BUDGET_PLAN](../STAGE4_QWEN_BUDGET_PLAN.md)。
2. **业务操作失败后的重试入口**：Ticket MCP 不可用时，审批已记录、运行结束并显示失败原因，但没有“重新执行”按钮/接口（接口是幂等的，可以安全补上）。
3. **HTTP 传输只在测试里用过**：Hub 默认 stdio；没有把 HTTP 接入 Compose，也没测 TLS（只允许回环地址，所以没做）。
4. ReplayCache 在子进程内存中，重启后清空（依赖 30 秒 TTL + 数据库唯一约束兜底）。
5. MCP 子进程冷启动约 1.3–4 秒（box/WSL），首次请求变慢；目前懒启动，没有在 lifespan 预热。
6. Mock 草稿描述会带上部分不相关的证据行（如 ORD-4001），是抽取式 Mock 的局限，真实模型需要另测。
7. 历史提交 9443490 曾误提交 `backend/src/data/uploads/` 下的演示语料副本（来自手动 prepare，内容就是 `evals/corpus`，不含任何密钥），已取消跟踪并加入 `.gitignore`；没有改写历史，因为已同步到 WSL。

## 4. 文件变更（stage-3.0.1..stage-4）
- 新增 `backend/src/opspilot/mcp/`：`contracts.py`（契约）、`identity.py`（调用者令牌）、`impl.py`（工具实现，含业务操作）、`runtime.py`（服务端运行时：验证、加载用户、限额、审计、故障注入）、`http_guard.py`、`client.py`（MCPHub）、`servers/{knowledge,ticket,diagnostics}.py`。
- 修改 `agent/graph.py`（incident_ticket 路径、`draft` / `revise` 节点、审批四种结果、`backend_tool`）、`agent/tools.py`（MCP 传输、新工具 spec、`query_mock_logs` 改名为 `query_system_logs`）、`agent/rules.py` / `llm.py` / `state.py` / `service.py`（`returned`、待审批队列）、`api/routes/agent.py`、新增 `api/routes/mcp.py`、`db/models.py`、`core/config.py`、`main.py`（lifespan 关闭 MCP 子进程）。
- 迁移 `backend/migrations/versions/20261102_0005_stage4_mcp_audit_approvals.py`。
- 前端 `src/api.ts`、`src/pages/Agent.tsx`、测试；E2E `frontend/e2e/mcp_e2e.py`、`scripts/e2e_windows.ps1`。
- 测试 `tests/mcp/`（conftest、evil_server、test_protocol、test_incident_flow、test_api_stage4）以及阶段 3 测试的期望调整（`executed`、transport=inprocess 默认、schema 新表、stage=4）。
- 脚本 `scripts/mcp_demo_client.py`、`scripts/build_stage4_notebook.py`；Notebook；评测数据集与结果；文档（本报告、ADR-004、预算方案、图、README、PROGRESS、API）。
- 依赖：`mcp>=2.3,<2.4`（uv.lock）。

## 5. 设计决定（摘要，详见 ADR-004）
1. mcp 2.3 + 自研 Hub，不用 adapter（版本不兼容，且需要带外身份）。
2. 身份放在 `_meta` 的短期签名令牌里，绑定 server/tool/参数哈希/jti；服务端从数据库重新加载用户。
3. 子进程最小权限：无 LLM Key、无 JWT 密钥、强制 mock LLM、付费 embedding 强制 mock。
4. 业务写操作只走 Ticket MCP `update_ticket_status`，需要数据库中的 admin 审批记录；恰好一次靠唯一约束，不靠检查点重放。
5. 退回修改生成新草稿版本（`parent_draft_id`），旧版置 `returned`；次数上限 2。
6. incident 场景的草稿参数由服务端根据核验后的回答拼装，模型拿不到建单工具。
7. 保留进程内传输作为单测回退（`agent_tool_transport=inprocess`，阶段 3 测试默认使用），生产默认 `mcp`。

## 6. 运行方式
```bash
uv run pytest -q                                   # 全部（Stage 4 测试自己拉起 MCP 子进程）
uv run pytest -q tests/mcp                         # 只跑 MCP
uv run python scripts/mcp_demo_client.py --as alice   # 发现 + 调用演示（读本项目 .env）
python -m opspilot.mcp.servers.diagnostics --transport http --port 18555   # 可选 HTTP：需 OPSPILOT_MCP_HTTP_TOKEN(≥32) 与 OPSPILOT_MCP_CALLER_SECRET，仅 127.0.0.1
uv run python evals/run_agent_eval.py --prepare --db-url "$OPSPILOT_TEST_DATABASE_URL" --tag local
```
