# ADR-003：阶段 3 Agent 技术选型

状态：已采纳（2026-11-02）

## 1. 编排框架：LangGraph StateGraph
- **选择**：LangGraph（显式节点 + `Command(update, goto)` 路由，`destinations=` 声明可达节点）。
- **备选**：LangChain AgentExecutor / `create_react_agent`（黑盒循环，难以插入证据评估与审批）；自研状态机（要自己实现检查点、中断恢复）；CrewAI / AutoGen（多 Agent 对话，超出需求）。
- **理由**：需要可审计的确定性控制流（守卫 → 分类 → 计划 → 检索 → 证据评估 → ReAct 工具 → 审批中断 → 生成 → 校验），并原生支持 checkpointer 与 `interrupt()`。
- **代价**：LangGraph API 仍在演进（版本锁定在 uv.lock）；`Command` 路由在静态图里显示为虚线，需另画注释版流程图。

## 2. 持久化：PostgresSaver，独立 schema `langgraph`
- **选择**：复用业务 PostgreSQL，检查点表放在 `langgraph` schema；Alembic 0004 只建空 schema，表由 `PostgresSaver.setup()` 在应用 lifespan 中创建（也可 `python -m opspilot.agent.checkpoint setup`）；Alembic env 忽略该 schema。
- **备选**：SQLite saver（不适合多进程容器）；Redis（多一个组件）；把检查点表写进 Alembic 迁移（与 LangGraph 自带迁移冲突，升级时漂移）。
- **代价**：两套迁移机制并存；`setup()` 使用 `CREATE INDEX CONCURRENTLY`，不能放在请求事务里（曾导致挂起，已移到 lifespan，测试中关闭自动 setup）。连接池需 autocommit、`prepare_threshold=0`；启用 `LANGGRAPH_STRICT_MSGPACK`。

## 3. 人工审批：`interrupt()` + 管理员 API
- 草稿工具只在用户**明确要求**建单时可用；`approval` 节点在 `interrupt()` 前无副作用；`apply_approval` 校验 admin、TTL（24h 过期）、条件更新与唯一约束，保证重复批准返回 409、草稿只写一次。
- **备选**：工具直接建单（不可接受）；轮询审批表（失去图内恢复语义）。

## 4. 工具层：ToolNode + ToolGuard
- 服务端白名单（plan.allowed_tools）→ Pydantic 校验 → 幂等键 → 只读工具超时/重试 → 输出包裹为不可信数据 → 记录 tool_calls。身份通过 `InjectedState` 注入，模型无法伪造。
- **备选**：MCP 工具（阶段 4 再外置，接口保持一致）。

## 5. 模型：Mock 默认，Qwen（OpenAI 兼容）可选
- 默认 `MockAgentLLM`（规则 + 抽取式生成），零成本、可复现，用于测试/评测/E2E；所有 Mock 结果在 API、前端与评测 JSON 中显式标注。
- `QwenAgentLLM` 经统一网关 `guarded_call`（授权号、预算预留、调用上限、输出钳制、并发、`max_retries=0`）。LLM 异常（超时/限流/解析失败）降级为规则路径并写入 `degraded`。
- **代价**：Mock 指标不能代表真实模型能力；真实评测需单独授权（见 STAGE3_QWEN_TEST_PLAN）。

## 6. 证据评估：规则优先，LLM judge 只能收紧
- `assess` 用规则检查锚点、主题词、值槽、单位/数值矛盾、必需工具；可选 judge 只能把“可回答”改为更保守的决定，丢弃其置信度。
- **理由**：防止模型以“看起来有道理”的方式放行无证据回答；代价是部分可回答问题被保守拒答（Mock 评测中误拒率 0，但真实模型下需复测）。
