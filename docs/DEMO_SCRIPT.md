# 演示脚本（阶段 5，约 8 分钟，全程 Mock、零费用）

准备：`docker compose --env-file .env -f infra/docker-compose.yml up -d`，执行种子与语料导入（见 README）。开发版 http://localhost:5173，生产版 http://localhost:5180（`--profile prod`）。演示账号密码见本地 `.env`（不要写进幻灯片）。

1. **登录与角色（1 分钟）**：先空提交看表单校验，再输错密码看统一错误提示。用 admin 登录，侧边栏 9 项；换 alice（普通业务用户），「用户与权限」「系统设置」不可见，直接输入 `/admin/users` 显示「无权访问」。
2. **仪表盘（1 分钟）**：说明每个数字都来自数据库；成功率旁显示分子 / 分母；p50 / p95 延迟；Mock 与真实调用分开计数（当前真实为 0）。
3. **知识库（1.5 分钟）**：admin 在「管理」创建知识库并授权；上传一份含错误码的文档，状态变成「已索引」；「检索测试」四种模式，提示 Mock embedding 没有语义理解；演示「更新」新版本与删除确认框。
4. **智能体工作台（2 分钟）**：alice 输入「PGW-xxxxx 支付网关熔断导致下单失败，请分析日志并帮我创建一个工单」。右侧实时显示节点、MCP 工具（search_knowledge、analyze_error_code、query_system_logs、get_service_status、create_ticket_draft）和证据，证据来自刚上传的文档；最后停在「等待审批」。刷新页面，会话和状态从 API 恢复。可以讲 SSE 断线重连（Last-Event-ID）。
5. **审批与工单（1.5 分钟）**：admin 在「工单中心 → 草稿审批」看证据，演示「驳回」确认框后取消，再「批准」→ 生成工单号。用 sam（技术支持工程师）把工单改为「处理中」并填写备注，事件时间线记录人工操作。回到仪表盘，工单数 +1。
6. **执行可视化与评测（1 分钟）**：打开该次运行的流水线图和事件日志；评测页 Mock / 真实分开展示。
7. **设置（0.5 分钟）**：付费接口「关闭（默认）」，API Key 只显示「是否已配置」。

自动化等价：`scripts/e2e_windows.ps1 -ProdUrl http://localhost:5180`（Windows Edge，会依次跑全部 E2E 并截图）。
