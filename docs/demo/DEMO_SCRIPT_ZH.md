# 演示脚本（3–5 分钟，中文讲解）

**模式：Mock（零费用）**。开发 UI：http://localhost:5173 · 生产 UI：http://localhost:5180 · API：http://localhost:8020  
账号：admin / alice / sam（密码见本地 `.env`，勿念出、勿截进幻灯片）。

## 流程（订单接口故障 → 检索 → 日志 → 错误码 → 方案+工单 → 审批 → 恢复）

| 步 | 操作 | 讲解词（约） |
|---|---|---|
| 0 | 打开登录页，用 alice 登录 | 「这是个人作品集项目 OpsPilot：面向技术支持的智能运维 Agent，默认 Mock，不调付费模型。」 |
| 1 | 进入「智能体工作台」，输入：`ORD-5003 支付超时导致下单失败，请分析日志并帮我创建一个工单` | 「用户描述订单 API 故障。Agent 会走 LangGraph：防护→分类→工具→证据→生成；工单必须人工审批。」 |
| 2 | 右侧看 SSE 事件：node / tool / evidence | 「流式是节点级事件，不是逐 token。可断线按 Last-Event-ID 续传。」 |
| 3 | 指出工具：`search_knowledge`、`query_system_logs`、`analyze_error_code`、`get_service_status`、`create_ticket_draft` | 「工具经真实 MCP stdio；调用带短时签名令牌，子进程拿不到 JWT/LLM Key。」 |
| 4 | 展示引用与草稿「等待审批」 | 「答案带引用；建单停在 interrupt，检查点在 Postgres。」 |
| 5 | 退出，用 admin 登录 → 工单中心 → 草稿审批 → 批准 | 「RBAC：普通用户不能自批。批准后 Ticket MCP 真正建单。」 |
| 6 | 回到执行可视化 / 可观测性 | 「可看节点轨迹、审计与 span；密钥只显示是否配置。」 |
| 7 | （可选）评测页 | 「Agent Mock 冻结集 final_test 任务成功率约 87%；v1 回归 45 条 100%。数字来自 evals/results，不是模型榜单。」 |

自动化等价：`scripts/e2e_windows.ps1`（含 stage5 全流程）。截图目录：`docs/assets/stage5/`、`docs/assets/stage7/`。

## 可复现录制方案（**本阶段不生成视频**）
1. 使用 Windows 游戏栏 / OBS，区域录制浏览器窗口，分辨率 1280×720，24–30fps。
2. 按上表 0→7 操作，总时长控制 3–5 分钟；口播可用本讲解词。
3. 录制前确认 `OPSPILOT_LLM_PROVIDER=mock`，关闭无关通知；密码用密码管理器填充，勿口述。
4. 成片建议导出 MP4（H.264）；文件名 `opspilot-demo-mock-YYYYMMDD.mp4`。
5. **未经 Teddy 授权，不上传公网、不声称仓库内已有视频。**
