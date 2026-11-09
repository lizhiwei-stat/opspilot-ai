# Stage 7 交付报告（v1.0.0-rc1）

**时间**：2026-11-09 Asia/Hong_Kong  
**授权消息**：t72u / pt72u  
**前置**：Stage 6 已验收；主库备份 `data/backups/pre_stage7_20261109115827.dump`（WSL 主工程）

---

## 1. 目标完成度

| # | 范围 | 状态 |
|---|---|---|
| 1 | 仓库清理 + 密钥扫描 + 许可证审计 + LICENSE | 完成（见下） |
| 2 | README.md + README.en.md（16 节）+ 图 + 实测数字 + 截图 | 完成 |
| 3 | 演示脚本 + 录制方案 + Edge e2e 验证 + stage7 截图 | 完成（**无视频文件**） |
| 4 | docs/INTERVIEW_DESIGN.md（11 主题） | 完成 |
| 5 | docs/career/* 求职材料 | 完成（标明个人作品集） |
| 6 | 全新环境验证（独立 compose/端口）后拆除 | 完成 |
| 7 | CHANGELOG / RELEASE_NOTES / 本地 annotated tag / STAGE7_REPORT | 完成（等 Teddy 发布授权） |

---

## 2. 密钥与隐私扫描

**工具**：gitleaks 8.21.2（用户空间二进制）+ 模式 grep；truffleHog 仅作辅助（GNU，非运行依赖）。

| 范围 | 结果 |
|---|---|
| 工作区 | 1 条：`tests/conftest.py` `TEST_SECRET` → **合成 JWT 测试密钥**，非 DashScope/生产密钥 |
| 全历史 | 同上 1 条；**未改写历史、未删标签** |
| `.env` | **从未入库** |
| 个人隐私 | 跟踪源码中未见真实个人 Gmail/手机；演示账号为 `*@opspilot.local` |
| 误报 | 延迟浮点触发的 phone 正则；测试夹具 `FAKE_KEY` |

详情：`docs/reports/stage7_scan/SECRET_SCAN.md`  
**待 Teddy 决策**：接受合成测试密钥留在历史，或另行指示（仍禁止擅自 rewrite）。若本机曾用真实云密钥，发布前轮换。

---

## 3. 许可证

- **项目 LICENSE**：MIT（与 `pyproject.toml` 一致）；理由见 `docs/LICENSE_DECISION.md`。**最终以 Teddy 确认为准**（可改 Apache-2.0）。
- **Python**（pip-licenses）：以 MIT/BSD/Apache-2.0 为主；LGPL：`psycopg*`、`fpdf2`（语料脚本）。摘要：`docs/reports/stage7_scan/pip_licenses.md`。
- **npm**（license-checker）：以 MIT/ISC/Apache-2.0 为主；未见 AGPL/SSPL。摘要：`docs/reports/stage7_scan/npm_licenses_summary.txt`。

---

## 4. 文档与求职材料路径

| 产物 | 路径 |
|---|---|
| 中文 README | `README.md` |
| 英文 README | `README.en.md` |
| 架构 / Agent 图 | `docs/diagrams/` + README mermaid |
| 演示脚本+录制方案 | `docs/demo/DEMO_SCRIPT_ZH.md` |
| Stage7 截图 | `docs/assets/stage7/demo_*.png` |
| 面试设计（11 题） | `docs/INTERVIEW_DESIGN.md` |
| 简历/亮点/ADR/问答/追问/答辩 | `docs/career/` |
| CHANGELOG | `CHANGELOG.md` |
| 发布说明 | `docs/RELEASE_NOTES_v1.0.0-rc1.md` |
| 本报告 | `docs/reports/STAGE7_REPORT.md` |

---

## 5. 全新环境验证（已实际验证）

**环境**：WSL Ubuntu-24.04，克隆至 `~/projects/opspilot-ai-s7fresh` @ `d6ea2ed`（文档提交点；本报告提交后 tag 指向含本文件的 commit）。  
**隔离**：`COMPOSE_PROJECT_NAME=opspilot_s7fresh`，端口 **55433 / 18021 / 15174 / 15181**（避开主栈与 zhanggui）。  
**配置**：自 `.env.example` 生成；mock 模式；本地新密码/JWT（**未打印**）。

| 步骤 | 结果 |
|---|---|
| compose up + migrate + seed/demo corpus | 成功 |
| 前端可达 | HTTP 200 |
| 业务演示 API（alice 草稿 → admin 批准） | 工单 `TCK-0004` |
| pytest | **269 passed，1 skipped** |
| vitest（frontend 容器） | **31 passed**（5 files） |
| Agent eval v1 Mock | **task_success_rate = 1.0**，failures=[] → `evals/results/20261109T040935Z_s7fresh_baseline_v1.json` |
| Retrieval eval Mock（dev 55） | hybrid R@5 **0.946**；hybrid_rerank:rule **0.957** → `…T041024Z_s7fresh_mock-hash-embedding-v1_dev.md` |
| tear down | `docker compose down -v` **仅** `opspilot_s7fresh`；主栈 `localhost:8020/health/live` 仍 200 |

Windows Edge e2e（主栈，Stage 7 演示截图前）：7 套件通过（含 stage5 全流程）；截图写入 `docs/assets/stage7/`。

---

## 6. 验证分类

### 已实际验证
- 主栈 Stage 5/6/7 相关 Edge e2e 与演示截图采集。
- Fresh clone：compose / migrate / seed / 演示 API / pytest / vitest / agent v1 / retrieval mock。
- 密钥扫描（工作区+历史）与依赖许可证扫描工具跑通。
- 主库 pre-stage7 备份存在；fresh 栈已拆除且主 API 存活。

### 仅静态检查
- README / career / INTERVIEW_DESIGN 文案与代码路径交叉引用（人工核对，未逐行跑文档测试）。
- LICENSE 文本与 decision note（未做律师审查）。
- npm/pip 许可证摘要解读（工具输出 + 人工扫一眼高风险牌照）。

### 尚未验证 / 明确未做
- **Git push**、仓库公开、公网部署。
- 付费 Qwen/DashScope 对比评测（Stage 6/7 默认禁止）。
- 演示 **视频**录制与上传（仅有录制方案）。
- 真实云密钥轮换（需 Teddy 在持有密钥的账号侧操作）。
- Apache-2.0 改版（若 Teddy 选择更换）。
- box 上 Node 20 的 vitest（已知；以 WSL/容器 Node 22 为准）。

---

## 7. 最终交付清单（发布授权前）

- [x] LICENSE + LICENSE_DECISION
- [x] 密钥/许可证扫描报告
- [x] 双语 README（16 节）+ 图 + 真实数字 + 截图
- [x] 演示脚本 + 录制方案 + stage7 截图（无视频）
- [x] INTERVIEW_DESIGN + career 材料
- [x] Fresh-env 验证记录 + 复现评测产物入库
- [x] CHANGELOG + RELEASE_NOTES
- [x] 本地 annotated tag `v1.0.0-rc1`（box + WSL ff-only bundle；**未 push**）
- [ ] Teddy：确认 LICENSE（MIT vs Apache-2.0）
- [ ] Teddy：确认是否接受历史中的合成 TEST_SECRET / 是否轮换本机曾用真实 Key
- [ ] Teddy：指定 push 远端与可见性后，再允许 push / 公开
- [ ] Teddy：若需要演示视频，另行授权录制

---

## 8. 硬约束遵守情况

未改写历史、未删标签、未 push、未公开、未公网部署、未付费 API、未读其他项目 `.env`、未 `bash -x` 打印密钥、未 sudo、未碰 zhanggui、未删 Windows bundles、未声称存在视频、里程碑已提交。
