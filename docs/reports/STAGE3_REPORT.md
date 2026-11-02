# 阶段 3 报告：LangGraph 企业 Agent（2026-11-02）

> 本阶段**未发起任何付费模型调用**。Agent 默认 Mock（规则 + 抽取式生成），所有 Mock 指标只说明流程、权限、工具、检查点、审批是否按设计工作，**不代表真实模型推理能力**。真实 Qwen 测试方案见 [STAGE3_QWEN_TEST_PLAN](../STAGE3_QWEN_TEST_PLAN.md)，等待单独授权。

相关文档：[设计](../STAGE3_DESIGN.md) · [Agent State](../AGENT_STATE.md) · [ADR-003](../adr/ADR-003-stage3-agent.md) · [API](../API.md#agent阶段-3--已实现) · [流程图](../diagrams/stage3_agent_graph.png) · Notebook `notebooks/langgraph_agent_walkthrough.ipynb`

## 0. 间歇性 401 根因与修复（先于阶段 3 编码）
- **现象**：Windows Edge E2E 约 1/6 次登录后第一个请求 401。
- **根因**：WSL2 虚拟机时钟会向后跳几秒（宿主同步），而 JWT 校验 `leeway=0`；令牌 `iat/nbf` 比后端当前时间“超前”约 6.2 s → `ImmatureSignatureError` → 401。通过只记录拒绝原因（不记录令牌内容）的诊断日志确认。
- **修复**（`1ab5122`）：`security.py` 加 60 s 有界时钟偏差容忍；`test_jwt_clock_skew.py` 回归测试（偏差内通过、超出拒绝、过期仍拒绝）。同时审计密钥加载：`.env` 固定为本仓库根目录，只接受 `OPSPILOT_` 前缀（`test_key_loading.py`），不会读取其他项目的 `.env`。
- **验证**：本轮在 Windows Edge 上共跑 7 次 E2E，登录与 API 调用均无 401（前 4 次因 E2E 脚本自身等待按钮文字的 bug 在 Agent 多轮步骤失败，修正等待条件后连续 3 次全部 PASS）。修复前为 6 次中 1 次 401。

## 1. 分里程碑
| 里程碑 | 内容 | 提交 |
|---|---|---|
| 3a | State / 规则 / 证据评估 / 5 个工具（search_knowledge、query_mock_logs、get_ticket、analyze_error_code、create_ticket_draft）+ ToolGuard；付费守卫扩展；迁移 0004 | `4e5bff7` `984fdb6` |
| 3b | 显式 StateGraph（15 节点，`Command` 路由，fail_safe，步数/时限/工具/ReAct/LLM 次数上限）；LLM 故障降级为规则；h029–h036 回归 | `37b5f2f` `f2f6c40` |
| 3c | PostgresSaver（schema `langgraph`）、AgentService、Agent API、HITL 审批（interrupt/TTL/幂等）、崩溃恢复；React Agent 工作台 | `70a1b27` `a6d29ee` |
| 3d | 模型层（Mock/Qwen，judge 可选）、41 场景评测、实时栈冒烟、Windows Edge E2E、Notebook、文档 | `f7b4505` … `HEAD` |

## 2. 已实际验证
**测试（box 与 WSL2 同一提交）**
- pytest：**217 passed, 1 skipped**（Agent 相关：test_graph 34、test_persistence 7、test_api_agent 4、test_agent_units 15，另有 JWT 偏差、key 加载、迁移漂移等）
- ruff check / format：通过；vitest：**14 passed**；`tsc -b`：通过（WSL 上在 frontend 容器内运行）

**WSL2 + Docker Compose（backend 8020 / pg 5433 / frontend 5173）**
- 重建后 backend healthy，alembic head = 0004；`/api/v1/version` → stage 3 / 0.3.0；种子新增 3 个演示工单
- 实时冒烟（`scripts/agent_api_smoke.py`，产物 `docs/reports/stage3_artifacts/`）：发起建单请求 → `awaiting_approval` → **重启 backend 容器** → 仍为 awaiting_approval → alice 审批 403 → admin 审批 200 → completed、草稿 approved；create_ticket_draft 只执行 1 次；重复审批 409；该 thread 11 个检查点
- PG 持久化：`langgraph` schema 4 张表（checkpoints / checkpoint_blobs / checkpoint_writes / checkpoint_migrations），当时 79 个检查点 / 6 个 thread
- Windows Edge 无头 E2E（`scripts/e2e_windows.ps1`）：登录 ×2、知识库/检索/问答、**Agent 工作台**（工具调用 + 引用 + Mock 标注、h029 拒答、多轮追问解析 TCK-0001、alice 发起草稿 → admin 批准）全部 PASS；截图 `docs/assets/stage3_agent_{tools,refusal,multiturn,approval_pending,approved}.png`

**Mock 评测（41 场景，`evals/results/20261102T095346Z_box.json`，⚠️ MOCK）**
| 指标 | 值 |
|---|---|
| 任务成功率 | 1.0（41/41） |
| 工具选择准确率 | 1.0（n=21） |
| 工具调用成功率 | 0.857（排除故意注入的故障 0.913） |
| 有证据支撑的回答率 | 1.0（n=20） |
| 正确拒答率 / 误拒率 | 1.0（n=13） / 0.0 |
| 越权执行 / 未审批副作用 | 0 / 0 |
| 平均工具调用 | 0.683 |
| 延迟 P50 / P95 | 33 ms / 64 ms |
| tokens（字符估算） | 1505 入 / 3604 出 |
| 费用 | ¥0 |
WSL2 上最终提交复跑：`evals/results/20261102T100044Z_agent_mock_wsl.json`，主要指标与 box 完全一致（任务成功 1.0、有证据回答 1.0、正确拒答 1.0、误拒 0、越权/未审批 0/0）。
**注意**：评测集在开发中被用于调试（首次 39/41，修正 a08 生成器与 a29 场景设计后 41/41），因此存在过拟合风险，不是 held-out 结果。

**Notebook**：`notebooks/langgraph_agent_walkthrough.ipynb` 在 box 上执行并保留输出（结构、路由、拒答、ACL、故障注入、多轮、审批 + 新连接池恢复、检查点表）。

## 3. 仅静态检查 / 单元测试（未在真实环境验证）
- `QwenAgentLLM`：仅用假 OpenAI 客户端测试（工具调用解析、JSON 输出、错误映射 timeout/rate_limit/refused/api/parse、网关拒绝）
- LLM judge（`agent_llm_judge`）：仅单元测试，“只能收紧”的逻辑已测
- 审批 24 h 过期：用伪造时间测试，未真实等待
- 流程图 PNG 由 mermaid-cli 渲染，与代码节点/边人工核对；`draw_mermaid()` 自动导出版见 `docs/diagrams/stage3_agent_graph.mmd`

## 4. 尚未验证 / 未完成
- **真实 Qwen 调用**（冒烟与完整评测）——待授权
- SSE 流式节点事件（原 API 设计中的 `/events`）未实现
- 真实建单 / MCP Server 属阶段 4；本阶段批准只记录审批与草稿状态
- 并发压测、多 worker 下的检查点竞争未测
- Mock 生成器是抽取式，回答可读性有限（如 “HTTP 500 怎么排查” 会摘录片段）

## 5. 关键决策
见 [ADR-003](../adr/ADR-003-stage3-agent.md)：LangGraph 显式图而非黑盒 ReAct；PostgresSaver + 独立 schema，setup 放 lifespan；`interrupt()` 审批前无副作用 + 唯一约束保证幂等；工具身份经 InjectedState 注入；规则证据评估优先，judge 只能收紧；Mock 默认、付费多重守卫。

## 6. 文件变更（自 `1ab5122^`，68 个文件，+11.2k 行）
后端：`agent/{state,rules,evidence,tools,llm,graph,checkpoint,service}.py`、`api/routes/agent.py`、`main.py`、`core/{config,security,logging}.py`、`llm/gateway.py`、`db/models.py`、迁移 `20261102_0004_agent_stage3.py`、`migrations/env.py`
前端：`src/pages/Agent.tsx`、`src/api.ts`、`App.tsx`、测试 `agent.test.tsx` `App.test.tsx`、`e2e/agent_e2e.py`
评测/脚本：`evals/agent/agent_eval_v1.jsonl`、`evals/run_agent_eval.py`、`scripts/{agent_api_smoke.py,seed_demo.py,e2e_windows.ps1,build_stage3_notebook.py}`
测试：`tests/agent/*`、`tests/unit/test_{agent_units,jwt_clock_skew,key_loading}.py` 等
文档：本报告、AGENT_STATE、ADR-003、STAGE3_QWEN_TEST_PLAN、API、diagrams、README、PROGRESS
> 过程中 `f7b4505` 误删了 11 个阶段 2.5 评测结果文件，已在后续提交中原样恢复。
