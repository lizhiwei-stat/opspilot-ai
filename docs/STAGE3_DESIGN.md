# Stage 3 技术方案：LangGraph 运维 Agent（**仅设计，未写 Agent 业务代码**）

版本核对（2026-11-02，PyPI + 官方文档）：`langgraph 1.2.19`、`langgraph-checkpoint-postgres 3.2.0`（依赖 `langgraph-checkpoint>=4.3,<5`、`psycopg>=3.2`、`psycopg-pool>=3.2`）。
API 依据：https://docs.langchain.com/oss/python/langgraph/interrupts ，https://pypi.org/project/langgraph-checkpoint-postgres/ 。Stage 3 开工时锁定这两个版本到 `uv.lock`。

## 1. 目标与边界

- 用户用自然语言描述运维问题（"下单接口 500，ORD-5003"），Agent：理解意图 → 检索知识库（**复用 Stage 2/2.5 的 `QAService`/`RetrievalService`，不另起一套知识库**）→ 必要时调用工具（查工单、查告警规则、起草工单）→ 给出带引用的答复；创建/修改工单必须**人工审批**。
- 不做：自动执行生产变更、任意 shell/HTTP 工具、自由联网。

## 2. StateGraph 与 AgentState

```python
class AgentState(TypedDict):
    run_id: str                 # = agent_runs.id，也是 checkpointer thread_id
    user_id: str                # 服务端注入，LLM 不可改
    messages: Annotated[list[AnyMessage], add_messages]
    intent: Literal["qa", "troubleshoot", "ticket", "chitchat", "out_of_scope"] | None
    evidence: list[dict]        # 来自 QAService 的引用（chunk_id、doc、page、quote、score）
    tool_results: list[dict]    # 每次工具调用的摘要（完整结果在 tool_calls 表）
    pending_action: dict | None # 待审批的工单草稿（ticket_drafts.id）
    steps: int                  # 已执行节点数
    llm_calls: int; tool_calls: int
    cost_cny: float             # 来自 ModelGateway 账本
    error: str | None
    final: dict | None          # answer + citations + confidence
```

节点（每个节点是纯函数 + 依赖注入的服务；节点内不直接 new 数据库会话）：

| 节点 | 作用 | 复用 |
|---|---|---|
| `guard_in` | 输入长度/注入启发式/限额检查；写 agent_runs | `rag/sanitize.py` |
| `classify` | 意图分类（LLM 结构化输出，Mock 时用规则） | ModelGateway |
| `retrieve` | 调 `QAService.answer(user, q)`，拿证据与拒答信号 | Stage 2.5 RAG（ACL、阈值、anchor、policy 全部生效） |
| `agent` | ReAct：LLM 决定"回答 / 调工具 / 再检索" | ModelGateway.chat + tool schema |
| `tools` | 执行白名单工具（见 §4），超时/重试 | 工具注册表 |
| `draft_ticket` | 生成工单草稿写入 `ticket_drafts`（状态 pending） | 已有表 |
| `approval` | `interrupt()` 等待人工批准（§6） | langgraph.types.interrupt |
| `create_ticket` | 审批通过后创建 `tickets`（幂等键 = draft_id） | 已有表 |
| `respond` | 组装答复；答案过 `rag/policy.check_answer` + 引用校验 | Stage 2.5 policy |
| `fail_safe` | 任何超限/错误的统一出口：返回"证据不足/需人工" | — |

## 3. 条件路由与循环（ReAct）

```
START → guard_in → classify ─┬─ out_of_scope/chitchat → respond → END
                             └─ qa/troubleshoot/ticket → retrieve → agent
agent ─┬─ tool_call      → tools → agent            (ReAct 循环)
       ├─ need_more_docs → retrieve → agent         (最多 2 次再检索)
       ├─ propose_ticket → draft_ticket → approval ─┬─ approved → create_ticket → respond
       │                                            └─ rejected/edited → agent / respond
       ├─ answer          → respond → END
       └─ 超限/错误        → fail_safe → respond → END
```

路由函数只读 state，不调用 LLM；`steps/llm_calls/tool_calls/cost_cny` 每次路由前检查（§7）。

## 4. Tool Calling

- 工具用 Pydantic 定义参数 schema，绑定到模型（OpenAI 兼容 tools 字段，Qwen 支持）；Mock 模式下由 `MockToolCallingLLM` 按脚本返回 tool_calls，保证测试确定性。
- 白名单（Stage 3）：`search_kb`（= RetrievalService，强制当前 user ACL）、`get_ticket` / `list_tickets`（只读，按 reporter/assignee 过滤）、`get_alert_rule`（只读）、`draft_ticket`（只写草稿）。**没有**直接创建工单的工具——创建只能走 `approval → create_ticket` 节点。
- 工具参数中的 `user_id` 等身份字段由服务端从 state 注入，LLM 给出的同名参数被丢弃。
- 工具输出视为不可信数据：进入 prompt 前用 `<untrusted_tool_result>` 包裹并做注入清洗（同 RAG 文档）。

## 5. 超时、重试与失败恢复

| 层 | 策略 |
|---|---|
| LLM 调用 | 单次超时 30s；网络/5xx/429 指数退避重试 2 次（已有 openai_compat 重试）；仍失败 → `fail_safe` |
| 工具 | 每个工具声明 `timeout_s`（默认 5s）与 `retries`（只读 2，写 0）；超时记录 tool_calls.status=`timeout` 并把错误摘要回给 agent，agent 可换工具或结束 |
| RAG | embedding 不可用 → 已有 keyword 降级并在 evidence 标记 `degraded` |
| 节点异常 | LangGraph `RetryPolicy`（只用于幂等节点：retrieve、只读工具）；非幂等节点不自动重试 |
| 进程崩溃 | checkpointer 已持久化每步状态，`resume_run(run_id)` 用同一 thread_id 继续；`create_ticket` 以 draft_id 为幂等键，重复执行不会产生两张工单 |

## 6. Human-in-the-loop：工单审批（服务端强制）

```python
def approval(state):
    draft = state["pending_action"]
    decision = interrupt({"type": "ticket_approval", "draft_id": draft["id"],
                          "summary": draft["title"]},
                         response_schema=ApprovalDecision)   # langgraph>=1.2.12
    return {"approval": decision}
```

- 遵循官方"interrupt 规则"：`interrupt` 前不放非幂等副作用（草稿在上一个节点写好）；不包在 bare try/except 里；一个节点只调用一次 interrupt。
- **审批不是信任客户端的布尔值**：恢复接口 `POST /api/v1/agent/runs/{id}/approval` 由服务端校验 ① 当前用户具有审批角色（不是发起人本人时可配置）② `approvals` 表写入审批记录（审批人、时间、决定、修改内容）③ 再以 `Command(resume={"approved": true, "approval_id": ...})` 恢复图。`create_ticket` 节点**再次**查库确认存在对应 `approvals` 行且 draft 状态为 pending，否则拒绝——即使有人伪造 resume 值也无法建单。
- 拒绝/修改：修改后的字段写回 draft，回到 agent 或直接 respond。
- 超时未审批：run 状态保持 `waiting_approval`；超过 24h 由定时任务标记 `expired`。

## 7. 最大步骤与调用限制

| 限制 | 默认 | 实现 |
|---|---|---|
| 图递归上限 | `recursion_limit=25` | LangGraph config |
| 节点步数 | 12 | state.steps，路由前检查 |
| LLM 调用 | 6 / run | state.llm_calls |
| 工具调用 | 8 / run，同一工具同参数 ≤2 | state + 去重 |
| 再检索 | 2 / run | state |
| 单 run 费用 | ¥0.50（真实 Qwen）| ModelGateway 每 run 账本；超出 → `BudgetExceeded` → fail_safe |
| 全局真实测试预算 | **¥30**（Stage 3 全部真实 Qwen Chat 测试） | 每个已批准测试任务一个 approval id；账本累计跨 run 统计，达到上限后拒绝，不自动提额 |

## 8. 持久化 Checkpointer 与 Alembic 共存

- 使用 `langgraph-checkpoint-postgres` 的 `PostgresSaver`（同步，FastAPI 线程池）或 `AsyncPostgresSaver`。官方要求：手动建连接时 `autocommit=True` 且 `row_factory=dict_row`；首次使用需 `.setup()` 建表；建议设置 `LANGGRAPH_STRICT_MSGPACK=true` 限制反序列化类型。
- **独立 schema**：checkpointer 表放在 `langgraph` schema（连接串 `options=-csearch_path=langgraph`，专用连接池），与业务表（`public`，Alembic 管理）分离。
- **迁移归属**：
  - `public.*`（含 agent_runs / agent_steps / tool_calls / ticket_drafts / approvals）—— 只由 Alembic 管理；
  - `langgraph.*` —— 只由 `PostgresSaver.setup()` 管理（它自带版本化迁移表）。Alembic `env.py` 的 `include_object` / `include_schemas=False` 排除 `langgraph` schema，`alembic check` 不会把它当成漂移（Stage 2.5 已用同样方式排除动态 HNSW 索引）。
  - 一个 Alembic 迁移只负责 `CREATE SCHEMA IF NOT EXISTS langgraph` 和授权；`setup()` 在部署脚本 `scripts/setup_checkpointer.py` 中显式执行（不在每个请求里执行），CI 中跑一次并校验表存在。
  - 升级 `langgraph-checkpoint-postgres` 时：先在测试库跑 `setup()`，再升级生产。
- `agent_runs.thread_id` = checkpointer thread_id，删除 run 时同时 `checkpointer.delete_thread(thread_id)`。

## 9. 执行轨迹（agent_runs / agent_steps / tool_calls）

- 每个节点用装饰器 `@traced(node_name)` 记录 `agent_steps`：seq、node、status、输入/输出**摘要**（不存完整 prompt，敏感值经 redact）、tokens、耗时、错误。
- 每次工具调用写 `tool_calls`：tool_name、arguments（脱敏）、result 摘要、status（ok/error/timeout/denied）、attempt、duration。
- 每次模型调用由 ModelGateway 写账本（provider、model、tokens_in/out（来自 API usage）、cost、latency、approval_id），并把 tokens 累加到 agent_runs。
- 前端 Stage 3 增加 "Agent 运行" 页：时间线展示节点、工具、审批等待点。

## 10. 真实 Qwen Chat 测试计划（付费，默认关闭）

- 预算上限 **¥30**；模型 qwen-plus（0.8/2.0 元每百万 token，非思考模式，2026-11-02 核对，见 `configs/model_prices.json`），必要时 qwen-flash 对照。
- 至少 **30 个真实 Agent 场景**（另有同等数量的 Mock 场景用于 CI）：知识问答 8、故障排查多轮 6、需要工具 6、工单起草+审批/拒绝/修改 5、越权与注入 3、工具超时/失败恢复 2。
- 每次调用记录：token（API usage）、成本、延迟、选择的工具、工具结果状态、任务是否完成、是否需要人工。
- 粗估：每场景 ≤6 次调用 × ~3k 输入 + 500 输出 token ≈ 每场景 ¥0.02，30 场景 ≈ ¥0.6；跑 3 轮 ≈ ¥2，远低于 ¥30。**执行前单独报告预计费用并取得批准**。
- `OPSPILOT_ALLOW_PAID_API` 默认 false；每个获批测试任务一个 approval id；Key 只来自环境变量，禁止写入 git/日志/报告（已有脱敏测试）。

## 11. Stage 3 验收标准

1. 图结构与上述节点/路由一致，有图结构单元测试（编译后的节点与边）。
2. Mock 模式下 ≥30 个确定性场景全部通过（CI，无网络、无费用）。
3. 真实 Qwen 场景（经批准后）≥30 个，报告任务成功率、工具选择正确率、平均步数、P50/P95 延迟、总费用（≤ ¥30，账本可审计）。
4. 工单创建 100% 经过审批；伪造 resume / 跳过审批的测试必须失败。
5. 达到步数/调用/费用上限时安全终止，返回 fail_safe 答复，并有测试。
6. 进程中断后可从 checkpoint 恢复，不产生重复工单（幂等测试）。
7. 跨用户 ACL：Agent 任何路径都不能返回无权限 KB 的内容（复用 Stage 2.5 ACL/注入测试集并扩展到工具）。
8. `alembic check` 干净；`langgraph` schema 不被 Alembic 管理；setup 脚本可重复执行。
9. 所有 agent_runs/steps/tool_calls 轨迹可在前端查看；日志与轨迹中无密钥（脱敏测试）。
