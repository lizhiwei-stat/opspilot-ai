# Agent State（阶段 3）

定义：`backend/src/opspilot/agent/state.py`。状态由 LangGraph PostgresSaver 按 `thread_id` 持久化（schema `langgraph`），同一会话的多轮问答共享一个 thread；每轮（turn）在 `guard_in` 重置轮内计数。

## 字段
| 分组 | 字段 | 归并（reducer） | 说明 |
|---|---|---|---|
| 身份 | `run_id` `thread_id` `user_id` `user_role` | 覆盖 | **只由服务端注入**，模型输出与工具输出不能改写；工具通过 `InjectedState` 读取身份做 ACL |
| 会话 | `messages` | `add_messages` | 跨轮累积（多轮记忆） |
| | `question` `turn` | 覆盖 | 当前轮问题与轮次号（`agent_runs` 唯一 `(thread_id, turn)`） |
| 决策 | `classification` `plan` `evidence` `assessment` | 覆盖 | Pydantic 模型 `Classification/Plan/Evidence/Assessment` 的 dict 形式；`plan.allowed_tools` 是本轮服务端白名单 |
| 工具 | `tool_records` `tool_outputs` | `add`（带 turn 标签） | 每次调用：call_id、参数摘要、状态 ok/error/timeout/denied/invalid/duplicate、错误分类、重试次数、耗时、幂等键；输出包裹为不可信数据 |
| 审批 | `draft` `approval` `draft_result` | 覆盖 | 草稿（待审批）、审批结论、副作用节点结果（幂等） |
| 结果 | `final` `status` `error` | 覆盖 | `FinalAnswer`：answered/refused/clarify/awaiting_approval/failed + 引用 + 拒答原因 + 策略违规 |
| 预算 | `steps` `llm_calls` `tool_calls` `react_rounds` `deadline` | 覆盖（每轮重置） | 上限：max_steps 24、max_tool_calls 6、max_react_rounds 3、max_llm_calls 8、单轮 60 s；超限转 `fail_safe` |
| 观测 | `node_trace` | `add` | 节点执行轨迹（前端「执行轨迹」） |
| | `usage` | 字典合并 | tokens、费用、各节点耗时 |
| 多轮 | `last_entities` | 覆盖 | 上一轮实体（错误码/工单号/服务），用于“它现在什么状态”这类追问 |
| 恢复 | `agent_idle` `degraded` | 覆盖 | 模型本轮未提出新工具调用；本轮采取的降级（如 `llm_timeout->rules`） |

## 不变量（有测试覆盖）
1. 身份字段只来自 JWT；`test_graph.py` 注入用例验证模型/工具文本无法提权。
2. `approval` 节点在 `interrupt()` 之前无副作用；恢复后 `apply_approval` 以条件更新 + 唯一约束保证草稿只落库一次。
3. 证据中的工具/文档文本一律视为数据，不作为指令。
4. 每轮计数在 `guard_in` 重置，跨轮只保留 `messages`、`last_entities` 与累计记录。

## 镜像表
`AgentService._persist` 把每轮状态镜像到业务表 `agent_runs`（状态、轨迹、证据、评估、工具次数、费用、is_mock）、`agent_steps`、`tool_calls`（call_id、幂等键）、`ticket_drafts`（唯一幂等键）、`approvals`。检查点表与业务表分开：前者由 LangGraph 管理，后者由 Alembic 管理（见 ADR-003）。
