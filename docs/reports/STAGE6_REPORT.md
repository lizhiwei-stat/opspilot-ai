# 阶段 6 报告：生产工程、系统评测与可靠性（Mock，零付费）

日期：2026-11-09（Asia/Hong_Kong）· 版本 **0.6.0** · tag `stage-6`（本地，未推送）  
阶段 5 已验收。本阶段**无付费调用**；真实 Qwen 对照见 [STAGE6_QWEN_COMPARE_PLAN.md](../STAGE6_QWEN_COMPARE_PLAN.md)（**待批准**，预算 ¥5 / 80 次）。

## 1. 已实际验证
| 项目 | 环境 | 结果 |
|---|---|---|
| 后端 pytest | box | **269 passed, 1 skipped**（含 stage6 12） |
| ruff check + format | box | 通过 |
| 前端 eslint / tsc / build | box | 通过 |
| 前端 vitest | WSL frontend 容器 Node 22 | **31 passed**；box 宿主 Node 20 与 undici/jsdom 不兼容（已记录） |
| Agent 评测 baseline v1（45） | box `opspilot_test` Mock | **task_success 100%**，tool_sel 100%，p50/p95/p99 = 42/1667/2151 ms，¥0 |
| Agent 评测 v2 final_test（31，冻结） | 同上 | **87.1%**（4 失败，见下），SHA 校验通过 |
| Agent 评测 v2 全量（103） | 同上 | **73.8%**（合成期望偏严，见失败分类） |
| 负载/可靠性 Mock | box `opspilot_s6dev_test`:18020 | 见 [STAGE6_LOAD.md](STAGE6_LOAD.md)；仪表盘/并发/RAG/Agent/SSE 成功率 100% |
| 安全矩阵 | box | 见 [STAGE6_SECURITY.md](STAGE6_SECURITY.md) |
| 可观测性 | box UI 截图 | [s6_observability.png](../assets/stage6/s6_observability.png)；`/metrics`、trace 头、脱敏测试通过 |
| KB 删除 + 工作台滚动 | 代码 + 截图 | 删除需确认 slug；工作台独立滚动 [s6_workspace_scroll.png](../assets/stage6/s6_workspace_scroll.png) |
| 备份恢复演示 | WSL 一次性库 | dump 2.9MB → restore → users=4 tickets=19 → DROP |
| 镜像体积（WSL 已有） | docker images | backend **1.36GB** · frontend_prod **74.2MB** · frontend_dev **646MB** |
| Compose | 健康检查 / stop_grace / restart / 本机回环 | 已加强；docker-compose-patterns / build-strategies 已阅读并应用 |
| CI 工作流文件 | `.github/workflows/ci.yml` | 已扩展（audit、docker build、alembic）；**未推送远端**；本地脚本 `scripts/run_ci_local.sh` |
| pip-audit | box | 无已知漏洞 |

## 2. 仅静态检查
- GitHub Actions 远端未跑（禁止 push）。
- nginx 900s SSE 超时未跑满。
- 生产镜像非 root：backend USER app、frontend_prod nginx-unprivileged（配置审查 + 既往运行）。
- npm audit 的 moderate 项未强制升级（eslint 10 工具链）。

## 3. 尚未验证 / 未完成
- **真实 Qwen 对照**：仅计划，未执行。
- box 宿主 Node 20 无法跑 vitest（需 Node ≥22）；以 WSL 容器结果为准。
- MCP 硬杀 / PG 闪断未在 HTTP 压测里对共享栈执行。
- 合成评测用例期望偏严（强制 `tools` 精确匹配等），未在冻结 final_test 上调参；完整 103 条成功率 73.8% **如实保留**。
- 未引入 Redis / 完整 OTel+Grafana（见 ADR-005）。

## 4. 架构审查结论（摘要）
见 [ADR-005](../adr/ADR-005-stage6-engineering.md)。**不**加 K8s/微服务；**不**加 Redis；可观测采用 structlog JSON + `trace_id`/`request_id` + 进程内 span + `/metrics`，持久轨迹仍在既有表。维护成本低，重启丢内存 span（文档已说明）。

## 5. Agent 评测与 Baseline 对比
| 集 | n | Task Success | Tool Sel Acc | Groundedness | Abstention | p50/p95/p99 ms | ¥/task |
|---|---:|---:|---:|---:|---:|---|---:|
| Baseline v1（阶段 5 场景，阶段 6 代码） | 45 | **1.00** | 1.00 | 1.00 | 1.00 | 42 / 1667 / 2151 | 0 |
| v2 final_test（冻结） | 31 | **0.871** | 0.80 | 1.00 | 1.00 | 59 / 1275 / 1909 | 0 |
| v2 全量 | 103 | **0.738** | 0.667 | — | — | 54 / 409 / 1849 | 0 |

说明：Mock 指标衡量图/权限/工具/审批，**不是**模型推理。final_test 在调参前冻结（`evals/agent/FROZEN_v2.sha256`）。相对 baseline，扩展集暴露合成期望过严与部分边界 phrasing；**未宣称有显著优化提升**——v1 回归仍为 100%。

失败分类（final_test 4 条）：tool_selection ×3、status ×1（权限/拒答 phrasing）。根因见结果 JSON `metrics.failures`。

数据集：103 条，覆盖 kb_qa / multi_doc / tool / multi_tool / multi_turn / unanswerable / permission / error_code / ticket_gen / approval_interrupt / service_failure / injection；split dev 56 / validation 16 / final_test 31。

## 6. 阶段 5 遗留清理
- `DELETE /api/v1/knowledge-bases/{id}` + UI 确认 slug；E2E 删除临时 KB。
- 工作台 `h-[calc(100vh-7rem)]` + 会话/消息/侧栏独立 `overflow-y-auto`。

## 7. 运行成本
- 本阶段 Mock：**¥0**。
- 申请中的 Qwen 对照：点估计 ≈ ¥0.16，**预算上限 ¥5**，硬封顶 80 次（官价 2026-11-09，qwen-plus 输入 ¥0.8 / 输出 ¥2 每百万 tokens）。

## 8. 提交
里程碑提交见 git log；最终 tag `stage-6`（box + WSL bundle ff-only）。
