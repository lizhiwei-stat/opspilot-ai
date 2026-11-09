# OpsPilot AI v1.0.0-rc1 发布说明

**类型**：个人作品集候选发布（Release Candidate）  
**日期**：2026-11-09（Asia/Hong_Kong）  
**默认模式**：Mock（离线、确定性、零费用）  
**许可**：MIT（候选；见 `docs/LICENSE_DECISION.md`，最终以 Teddy 确认为准）

## 本候选包含什么
- 完整本地可运行栈：FastAPI + LangGraph + PostgreSQL/pgvector + MCP stdio + React 管理台 + Compose。
- 冻结评测集与可追溯结果（Agent / Retrieval），以及 Stage 5–7 截图与演示脚本。
- 面向求职的 README / 面试设计 / career 材料；**不包含**演示视频文件（仅录制方案）。

## 刻意不包含 / 未做
- 未推送远端、未公开仓库、未公网部署。
- 未改写 Git 历史、未删除标签。
- Stage 6/7 **未跑付费** DashScope/Qwen 对比（计划文档保留）。
- 无 K8s / Redis / Grafana 生产栈；无真实企业用户/收入数据。

## 安装（摘要）
见根目录 `README.md` §12。务必从 `.env.example` 生成本地密钥，**勿**提交 `.env`，**勿** `bash -x` 加载脚本。

## 关键实测数字（可追溯）
| 项 | 结果 | 来源 |
|---|---|---|
| Agent v1 Mock（45） | 任务成功 100% | `evals/results/20261109T033115Z_stage6_baseline_v1_mock.json`；fresh 复现 `…T040935Z_s7fresh_baseline_v1.json` |
| Agent v2 final_test（31） | 87.1% | `…T033130Z_stage6_v2_final_mock.json` |
| Agent v2 all（103） | 73.8% | `…T033148Z_stage6_v2_all_mock.json` |
| Retrieval Mock hybrid R@5 | 0.946 | `docs/reports/STAGE2_5_TABLES.md`；fresh `…T041024Z_s7fresh_mock-hash-embedding-v1_dev.md` |
| Fresh pytest | 269 passed / 1 skipped | Stage 7 全新环境 |
| Fresh vitest | 31 passed | Stage 7 全新环境 |

Mock 指标验证流水线正确性，**不**代表真实大模型推理能力。

## 安全提醒（发布前）
1. 历史中存在合成 `TEST_SECRET`（`tests/conftest.py`）——非云厂商密钥；是否轮换/接受由 Teddy 决定（**禁止**未经同意改写历史）。
2. 若曾在本机 `.env` 使用过真实 API Key，发布前轮换该 Key。
3. 确认 `LICENSE` 选择（MIT vs Apache-2.0）。
4. 确认 push 目标与可见性（private/public）。

## 升级说明
从 Stage 6 工作树快进即可；全新克隆按 README。数据库迁移沿用现有 Alembic 链。

## 致谢
个人工程；依赖社区开源组件（见许可证审计摘要）。
