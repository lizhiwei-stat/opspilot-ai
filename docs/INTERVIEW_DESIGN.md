# 面试向系统设计说明（结合真实代码与实测）

本文回答 Stage 7 要求的 11 个主题。数字均指向仓库内评测产物；Mock 指标说明流程正确性，不夸大模型能力。

## 1. 为什么选择 LangGraph
需要**显式多节点**（防护、分类、工具、证据、生成、校验）与**可中断审批**。LangGraph 的 `interrupt` + `PostgresSaver` 把「等待管理员」做成一等状态，而不是自造状态机。代码：`backend/src/opspilot/agent/graph.py`、`checkpoint.py`、`service.py`。

## 2. 为什么 PostgreSQL + pgvector
同一库承载业务表、向量、检查点与审计，备份/迁移一条链；本地 Compose 运维成本低。阶段 6 ADR 明确**不**为单机演示引入 Redis。代码：`infra/docker-compose.yml`、`backend/migrations/`。

## 3. RRF 融合为什么有用
关键词与向量失败模式不同。Mock 评测（55 条）上 hybrid Recall@5 **0.946** 高于纯 vector **0.888** / keyword **0.934**（`STAGE2_5_TABLES.md`）。实现：`backend/src/opspilot/rag/` 中 RRF。

## 4. Reranker 的效果与代价
rule rerank：R@5 **0.957**，P95≈11ms。cross-encoder：R@5 **0.969**，P95≈**2.0s**。适合离线/高准场景，不适合默认在线路径。同表见上。

## 5. MCP vs 普通 Function Calling
Function Calling 把工具留在模型进程内；本项目用 **MCP stdio 子进程**，工具实现与密钥隔离，调用带绑定参数哈希的短时令牌，Server 从 DB 重载用户再做 ACL。审计表 `mcp_tool_audit`。见 ADR-004、`backend/src/opspilot/mcp/`。

## 6. Checkpointer 如何恢复
`PostgresSaver` 持久化图状态；审批后 `resume` 从检查点继续，而不是重放用户问题。服务重启后仍可恢复（E2E / 阶段 3–4 烟雾测验证过）。`agent/service.py` 的 `approve` / `resume`。

## 7. 如何防止工具重复执行
工具记录写入状态；去重/幂等键与草稿唯一约束；评测统计 `deduplicated`。Ticket 审批执行有「一审批最多一次」约束（`ticket_events`）。

## 8. 如何做权限隔离
JWT 角色（admin/support/user）+ KB visibility/ACL；无读权限返回 **404** 不暴露存在性；MCP 与 API 双层校验。安全测：alice 看不到 `hr-confidential`（`tests/stage6/test_security_matrix.py`）。

## 9. 如何降低幻觉
证据阈值拒答；引用校验；策略扫描（密钥形态、动作宣称等）；Mock 抽取式生成。Agent 冻结集上 groundedness / abstention 在 Mock 规则下可到 1.0（见 stage6 final 结果 JSON）——**仍不代表真实模型**。

## 10. 如何控制 Token 与费用
默认 Mock；`ModelGateway` 要求 `ALLOW_PAID_API`、approval id、预算、max calls；设置页只暴露 `api_key_configured`。账本经 redact。Stage 6/7 **未跑付费**。

## 11. 如何评估 Agent 是否真正完成任务
`evals/run_agent_eval.py`：task success（检查项全过）、工具选择/执行、拒答、时延分位、失败 taxonomy。数据集分 dev/validation/final_test 并冻结 SHA。baseline v1=100%；final_test=87.1%；全量=73.8%（合成期望偏严，报告已说明）。
