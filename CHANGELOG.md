# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，版本号参考 SemVer。  
**说明**：条目描述本仓库实际交付内容；不声称企业生产用户量、收入或公网部署。

## [1.0.0-rc1] — 2026-11-09

### Added
- 开源交付材料：MIT `LICENSE`、`docs/LICENSE_DECISION.md`、双语 README（中/英，16 节）、架构与 Agent Mermaid 图。
- 演示：`docs/demo/DEMO_SCRIPT_ZH.md`（3–5 分钟讲解词 + 可复现录制方案，**不含视频文件**）与 `docs/assets/stage7/` 实测截图。
- 面试设计：`docs/INTERVIEW_DESIGN.md`（11 主题，绑定真实代码路径与评测数字）。
- 求职材料：`docs/career/`（简历描述、亮点、ADR 摘要、20 题问答、10 追问、答辩讲稿）；标明个人作品集。
- 发布说明：`docs/RELEASE_NOTES_v1.0.0-rc1.md`、本 CHANGELOG、`docs/reports/STAGE7_REPORT.md`。
- 密钥/许可证扫描摘要：`docs/reports/stage7_scan/`。
- 全新环境复现评测产物：`evals/results/20261109T040935Z_s7fresh_baseline_v1.json`、`…s7fresh_mock-hash-embedding-v1_dev.md`。

### Changed
- 包版本 `0.6.0` → `1.0.0rc1`，`STAGE = 7`。
- `.gitignore` 卫生（Stage 7 清理临时调试产物规则）。

### Security
- 工作区 + 全历史 gitleaks/模式扫描：仅合成 `TEST_SECRET`（`tests/conftest.py`）；`.env` 未入库。**未改写历史**；发布前需 Teddy 确认是否轮换/接受。
- 依赖许可证审计（pip-licenses / license-checker）摘要入库；运行依赖以 MIT/BSD/Apache-2.0 为主。

### Verified (fresh env)
- WSL 独立 Compose 项目 `opspilot_s7fresh`（端口 55433/18021/15174/15181），mock 模式：compose up → migrate → seed → 业务演示 API（approve→工单）→ pytest **269 passed / 1 skipped** → vitest **31 passed** → Agent v1 Mock **100%** → retrieval Mock hybrid R@5 **0.946**。
- 仅拆除 `opspilot_s7fresh` 及其 volumes；主栈保持可用。

## [0.6.0] — 2026-11-09（Stage 6）

可观测性、KB 删除、Agent evals v2、安全矩阵、负载可靠性、本地 CI、工程文档。详见 `docs/reports/STAGE6_REPORT.md`。

## [0.5.0] — Stage 5

三角色 Web 平台、SSE、审批 UI、Edge E2E。详见 `docs/reports/STAGE5_REPORT.md`。

## 更早

Stage 1–4：基础后端、RAG/pgvector、LangGraph Agent、MCP 工具与审计。标签见 `git tag`。

<!-- push URL TBD after Teddy authorizes remote -->
