# 阶段 5 报告：企业级 Web 平台（Mock 模型，零付费调用）

日期：2026-11-08（box 时钟）· 版本 0.5.0 · tag `stage-5`（本地，未推送）
范围：在阶段 4 后端之上做完整 React 平台。**没有新写第二套 Agent 或 RAG**，前端全部调用已有服务；后端只补 SSE 事件、平台聚合与管理接口。
本阶段**没有任何付费调用**，也没有续跑阶段 4 获批但未执行的 Qwen 测试。

## 1. 已实际验证
| 项目 | 环境 | 结果 |
|---|---|---|
| 后端测试 `uv run pytest -q` | box、WSL（连接 compose 的 opspilot_test） | **257 passed, 1 skipped**（两边一致）；新增 tests/stage5：SSE 5、平台接口 7、迁移 0006 往返（一次性数据库） |
| ruff check + format --check | box、WSL | 通过 |
| 前端 eslint（0 warning）· tsc · vitest · build | box；WSL 的 frontend 容器内 | 通过；vitest **31 passed** |
| 迁移 0006（agent_events、admin_audit、support 角色） | WSL 主库，迁移前备份 `data/backups/pre_stage5_20261108121146.dump`（未提交） | upgrade 成功，`/health/ready` 显示 revision 0006 |
| Windows Edge E2E（`scripts/e2e_windows.ps1 -ProdUrl …`），exit 0 | Windows 11 Edge → WSL compose | 7 组全部通过：登录 admin / alice PASS；rag_e2e PASS；agent_e2e ok；mcp_e2e ok；**stage5_e2e 开发版 (5173) ok、生产版 (5180) ok** |
| stage5_e2e 关键检查 | 同上 | 表单校验与错误密码；admin 建知识库并上传含唯一错误码 PGW-xxxxx 的文档 →「已索引」；检索测试命中；**SSE 第 2 条后人为断开，重连请求 Last-Event-ID = [0, 2]，19 条事件无缺无重**；alice 运行调用 5 个 MCP 工具，证据来自刚上传的文档，停在等待审批；刷新后会话恢复；alice 访问管理页显示无权访问、API 403；admin 驳回确认框 → 取消 → 批准建单（如 TCK-0014）；sam（support）改为处理中；仪表盘工单数 +1；重复登录无 401；删除文档确认框；控制台无意外错误（预期的 401/403 除外） |
| 生产构建 | WSL `--profile prod`，nginx-unprivileged，read_only | `/` 200、SPA 回退 200、`/api` 代理 200、CSP / X-Frame-Options 等头存在；SSE location 关闭缓冲 |
| 端口 | WSL | 5173、5180、8020、5433 均只监听 127.0.0.1 |
| 截图 | Edge 实拍 | `docs/assets/stage5/*.png`（13 张）、`docs/assets/stage5/prod/`（4 张）；阶段 2–4 回归截图同步刷新 |

## 2. 仅静态检查（未在运行环境单独验证）
- 后台线程执行与取消在并发很高时的表现：只有单元测试和单用户 E2E，没做压力测试。
- 心跳 `: hb` 与 `event: timeout`（max_s）：单元测试覆盖，浏览器里没有等到超时。
- nginx 900 秒 SSE 超时：只检查了配置，没有跑满 15 分钟的连接。
- 普通用户 / support 在仪表盘上的统计范围：代码与单元测试覆盖，E2E 只截了 admin 的仪表盘。
- 迁移 0006 的 downgrade：只在一次性测试库验证，没在主库执行。

## 3. 尚未验证 / 未完成
- **真实 Qwen 模型**：未调用（按要求）。工作台、仪表盘的「真实模型」计数为 0。
- 逐 token 流式：未做，SSE 是节点 / 事件级。
- 知识库删除接口：没有，E2E 每次会留下一个空的公开知识库 `e2e-s5-*`（文档已删）；E2E 产生的测试工单也留在主库。rag_e2e 已改为显式选择知识库，不受影响。
- 工作台左侧会话列表很长时整页变长（截图可见），应改为独立滚动。
- 无障碍只做了基础（landmark、aria-label、跳转链接），没有用工具审计。
- GitHub CI 未运行；未部署公网。

## 4. 主要设计决定
- **SSE 用数据库事件表**（不是内存队列）：重启、重连都能按 seq 续读；代价是每个节点一次写入。
- **fetch 流代替 EventSource**：EventSource 不能带 Authorization 头，不想把 JWT 放进 URL。
- **协作式取消**：只在节点边界停止，不强杀 MCP 子进程，避免半写状态。
- **token 存 sessionStorage**：关闭标签即失效，401 统一登出并提示会话过期。
- **生产镜像**：多阶段构建，nginx 非 root、只读根文件系统，安全头放 server 级（location 里用 `expires`，避免 add_header 覆盖继承）。
- 评测页不同数据集、Mock 与真实不合并平均；缺失指标显示「—」而不是 0。

## 5. 过程问题（如实记录）
- **Docker 相关 skill 不存在**：要求先加载 docker-compose-patterns / docker-build-strategies，查找后无此 skill，按保守方式修改（只新增 prod compose 文件和 Dockerfile，dev compose 只加一个只读挂载和一个环境变量）。
- **密钥泄露到会话输出**：在一次调试中对会 `source .env` 的脚本用了 `bash -x`，导致 OPSPILOT_LLM_API_KEY 和种子账号密码出现在本次工具输出里。没有写入任何文件、提交或日志文件。**建议轮换 DashScope / Qwen Key，并修改种子密码**。之后不再对加载 .env 的脚本使用 `-x`。

## 6. 提交
fc09176 后端 → 5b9ef65 前端 → ccbff7f / bb85437 / 2b3829d 生产构建与修复 → 46eeb40 E2E → 811e9fe、b77ff2c 修复 → 文档提交，tag `stage-5`（box 与 WSL，经 git bundle ff-only 同步）。
