# ADR-004：阶段 4 MCP 工具接入与审批工作流

状态：已采纳（2026-11-02）

## 1. 版本核对（2026-11-02 在 PyPI 实查）
| 包 | 选用版本 | 发布时间（HKT） | 说明 |
|---|---|---|---|
| `mcp`（官方 Python SDK） | **2.3.0**，约束 `>=2.3,<2.4` | 2026-10-03 06:06 | v2 稳定线；协议版本 `2026-07-28`；`MCPServer` / `Client` / `ToolError` / `_meta` / Streamable HTTP + `TransportSecuritySettings` |
| `mcp` 1.x | 1.30.0（未选） | — | 仅维护修复 |
| `langchain-mcp-adapters` | **未使用** | 0.3.2：2026-08-06 14:15 | `requires_dist` 为 `mcp<2.0.0,>=1.24.0`，与 mcp 2.x **不兼容** |
| `langgraph` | 1.2.19（沿用阶段 3） | 2026-10-31 06:23 | `interrupt()` / `Command(resume=)` / PostgresSaver 不变 |
| `langgraph-checkpoint-postgres` | 3.2.0（沿用） | — | — |
| `langchain-core` | 1.7.3（沿用） | — | — |

## 2. 决策：mcp 2.3 + 自研轻量 Hub，而不是 adapter
- **选择**：服务端用 `mcp.server.MCPServer`（每个工具有显式 Pydantic 输入/输出模型和 `ToolAnnotations`）；客户端用 SDK `Client`，在 `opspilot.mcp.client.MCPHub` 里做连接管理、发现、调用。LangGraph 侧不改图的结构，`ToolGuard._execute` 根据 `agent_tool_transport` 选择 MCP（默认）或进程内（单测回退）。
- **备选 A**：mcp 1.x + langchain-mcp-adapters。可直接把 MCP 工具转成 LangChain Tool，但 1.x 只做维护；adapter 把工具参数原样交给模型，**身份只能放进参数**或依赖连接级头，与“身份由服务端注入、不来自 LLM 参数”的要求冲突。
- **备选 B**：等 adapter 支持 mcp 2.x。时间不可控。
- **代价**：多 ~500 行 Hub 代码（后台事件循环线程、每个 server 一个 worker、崩溃后自动重启）；需要自己测试协议行为（`tests/mcp/test_protocol.py`）。

## 3. 身份与授权：签名调用者令牌放在 `_meta`
- 每次 `tools/call` 由后端签发 HS256 令牌（密钥 = HMAC(JWT 密钥, "opspilot-mcp-caller-v1")，子进程拿不到 JWT 密钥本身），声明 `aud=mcp:<server>`、`tool`、参数 SHA-256、`sub`/`role`/`thr`/`run`、`jti`、TTL 30 秒。
- 服务端校验：缺失 / 无效 / 过期 / 发给别的 server / 别的 tool / 参数被改 / jti 重放 → `[permission]` 错误并写审计；通过后**从数据库重新加载用户**（角色不信令牌声明），再执行原有 ACL（KB 可见性、工单归属）。
- 工具 schema 里没有 user_id；模型或攻击者传入多余字段会被 SDK 参数校验拒绝。
- **备选**：连接级 Bearer（不能区分同一连接上的不同用户）；把 user_id 放参数（违反要求）。

## 4. 业务写操作只走 Ticket MCP 的 `update_ticket_status`
- 审批通过后，图中的 `apply_approval` 节点以**审批人**身份调用 `update_ticket_status(draft_id, approval_id)`；服务端行锁草稿并校验：审批记录存在且为 approved、审批人是在职 admin、调用者是 admin 或草稿创建者、草稿载荷哈希一致。
- 恰好一次靠数据库，而不是依赖检查点重放：`tickets.source_draft_id UNIQUE`、`ticket_events.approval_id UNIQUE`、草稿状态条件更新；重复调用返回 `deduplicated=true`（已测试）。
- `revise_ticket_draft` 只给后端使用（退回修改时以草稿创建者身份生成新版本，`parent_draft_id` 指向旧版，旧版置为 `returned`），不在模型的工具列表里。
- incident_ticket 场景里 `create_ticket_draft` 由 `draft` 节点在**服务端**根据已核验的方案拼装参数，模型不会被提供建单工具。

## 5. 传输
- 默认 stdio（后端拉起子进程，`python -m opspilot.mcp.servers.<name>`，无 shell，最小环境变量）。
- 可选 Streamable HTTP：只允许绑定 127.0.0.1/::1/localhost；`Authorization: Bearer`（≥32 字符，常量时间比较）→ 否则 401；带 Origin 且不在白名单 → 403；SDK DNS-rebinding 保护（Host 白名单）；请求体 64 KiB。Bearer 只认证进程，用户身份仍需 `_meta` 令牌（已测试：有 Bearer 无令牌 → 拒绝）。

## 6. 不可信 MCP Server 的处理
- Registry 白名单（server 名 → 模块），握手后校验 `server_info.name`；`tools/list` 中不在契约里的工具忽略；输入 schema 属性集合与契约不完全一致的工具忽略（记录 `schema_mismatch`）。
- 输出：大小上限（`mcp_max_output_bytes`，客户端 2 倍余量）、按契约 Pydantic 校验、文本仍按不可信证据处理（沿用阶段 3 的注入清洗与 flagged）。
- 超时（`mcp_call_timeout_s`）、连接超时、进程崩溃 → 标记不可用，下次调用自动重启。

## 7. 已知代价 / 后续
- 业务操作失败（如 Ticket MCP 不可用）时，审批已记录、运行结束并提示失败，**目前没有“重试执行”按钮**；因为接口幂等，可以安全重试（阶段 5 补 UI/接口）。
- 每个后端进程各有一组 MCP 子进程（uvicorn 单 worker 下 3 个）；多 worker 时进程数成倍增加。
- ReplayCache 在内存中（子进程重启后清空），但令牌 TTL 只有 30 秒，且写操作另有数据库唯一约束兜底。
