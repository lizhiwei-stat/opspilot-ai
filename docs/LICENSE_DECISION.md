# 许可证决策说明（Stage 7）

## 项目许可证
- **选择：MIT**（与 `pyproject.toml` 中 `license = { text = "MIT" }` 一致）
- **理由**：个人作品集 / 求职展示项目，MIT 允许他人学习与复用，义务清晰；不强制衍生项目开源。
- **待 Teddy 最终确认**：若更倾向专利授权条款，可改为 Apache-2.0（需更新本文件与 `LICENSE`）。在确认前，仓库按 MIT 放置 `LICENSE` 文件作为候选。

## 依赖审计摘要（2026-11-09）
- Python（`pip-licenses`，环境内已安装包）：以 MIT / BSD / Apache-2.0 为主。
  - **LGPL-3.0**：`psycopg*`（数据库驱动，动态链接常见用法）、`fpdf2`（仅用于生成演示语料 PDF 的脚本）。
  - 扫描工具 `truffleHog`（GNU）仅为本地安全扫描临时安装，**不是**项目运行依赖。
- npm（`license-checker`，含开发依赖）：以 MIT / ISC / Apache-2.0 为主；未见 AGPL/SSPL。
- 完整机器可读输出保存在构建机 `/tmp/s7scan/`（不提交密钥）；摘要写入 `docs/reports/STAGE7_REPORT.md`。

## 合规注意
发布到公开 GitHub 前应再跑一次 `pip-licenses` / `license-checker`，并确认 `LICENSE` 文本与 Teddy 最终选择一致。
