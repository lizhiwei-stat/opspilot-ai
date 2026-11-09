# 简历项目描述（中文，可直接改写）

**OpsPilot AI — 智能运维支持 Agent 平台（个人作品集）**  
技术栈：FastAPI / LangGraph / PostgreSQL·pgvector / MCP / React / Docker Compose

- 设计并实现「知识库 RAG + MCP 诊断工具 + 工单草稿人工审批」的 LangGraph 工作流，审批中断可经 Postgres 检查点恢复。
- 完成企业知识库 ACL、RRF 混合检索与重排；Mock 评测 hybrid Recall@5=0.946（55 条，可追溯结果文件）。
- 接入三个 MCP Server（知识/工单/诊断），工具调用带签名令牌与审计表，避免子进程持有 LLM Key。
- 交付三角色 React 管理台、SSE 节点事件流、可观测性页与本地 Compose 生产前端镜像。
- 建立 ≥100 条 Agent 评测集（冻结 final_test）；Mock baseline 45 条任务成功率 100%，冻结集 87.1%。
- 补充安全矩阵测试、依赖许可证审计、密钥扫描报告与从零环境验证清单（见 Stage 7 报告）。

**不要写**：生产 DAU、营收、未发生的公有云部署。
