# 第二阶段报告：企业知识库 RAG

- 日期：2026-11-02（香港时间）。验证环境：Teddy 的 Windows + WSL2 Ubuntu 24.04，Docker Compose 项目 `opspilot`。
- 交付 tag：`stage-2`（阶段 1 的 `stage-1`、`stage-1.1`、`stage-1.2` 均保留）。没有推送到任何远程仓库，没有公网部署，也没有调用任何付费 API。
- 设计与验收标准见 [STAGE2_DESIGN.md](../STAGE2_DESIGN.md)，评测细节见 [STAGE2_EVAL.md](STAGE2_EVAL.md)。

> 诚实声明：默认的 mock embedding **没有语义**，mock 回答器**没有大模型**，评测语料和问题都是**合成数据**。下面"已实际验证"指的是代码和管线在真实 PostgreSQL / 浏览器上跑通，并不是说 RAG 效果已经达到生产水平。

## 1. 环境（WSL2 实测）

| 项 | 值 |
|---|---|
| Compose 服务 | `postgres`：pgvector/pgvector:0.8.7-pg17-trixie（127.0.0.1:5433）；`backend`：127.0.0.1:8020；`frontend`：127.0.0.1:5173 |
| 数据卷 | `opspilot_pgdata`，以及新增的 `opspilot_uploads`（原始文件，按 `<kb_id>/<sha256><ext>` 存储） |
| 迁移 | `alembic current` → `0002 (head)`；`alembic check` → `No new upgrade operations detected.` |
| 宿主测试 | `uv run`（Python 3.11.17），连接 compose 中的 `opspilot_test` 库 |
| 浏览器 E2E | Windows 无头 Edge（Playwright 1.64.1）。WSL 内的 Chromium 缺 `libasound.so.2`，安装需要 sudo，所以没有使用 |

## 2. 已实际验证（WSL2，原始输出节选）

最终验证提交：`7beff6d`（之后只追加了文档和产物）。

```text
$ docker compose --env-file .env -f infra/docker-compose.yml ps
backend  | opspilot-backend | Up 16 seconds (healthy) | 127.0.0.1:8020->8000/tcp
frontend | …                | Up 5 minutes            | 127.0.0.1:5173->5173/tcp
postgres | pgvector/pgvector:0.8.7-pg17-trixie | Up 31 minutes (healthy) | 127.0.0.1:5433->5432/tcp
ready 200 0.020898s
$ curl -s localhost:8020/health/ready      # 检查项 migrations: "alembic revision 0002"
$ curl -s localhost:8020/api/v1/system/version
{"name":"OpsPilot AI","version":"0.2.0","stage":2,…,"embedding_provider":"mock","embedding_model":"mock-hash-embedding-v1","embedding_dim":1024,
 "rag":{"chunk_size":500,"chunk_overlap":80,"rrf_k":60,"reranker":"lexical-overlap-v1","qa_min_evidence":0.5}}

$ docker compose … exec backend python scripts/load_demo_corpus.py      # 首次运行
hr-confidential  oncall-compensation-policy.md              indexed   chunks=3
order-platform   db-connection-pool-tuning.md               indexed   chunks=4
order-platform   deployment-rollback-guide.pdf              indexed   chunks=3
…（共 13 篇）
total=13 failed=0
$ （再次运行）→ 13 篇全部 duplicate（内容哈希去重），total=13 failed=0

$ OPSPILOT_REQUIRE_DB=1 uv run pytest -q
110 passed in 5.22s                     # real 0m7.374s
$ uv run ruff check . && uv run ruff format --check .
All checks passed!
69 files already formatted
$ docker compose … exec frontend npm test
 Test Files  2 passed (2)
      Tests  10 passed (10)
$ docker compose … exec frontend npm run build
✓ built in 176ms
$ make notebook          # 生成 + nbconvert --execute --inplace
[NbConvertApp] Writing 25171 bytes to notebooks/rag_walkthrough.ipynb      # 12 个代码单元全部有输出，0 个错误
$ powershell -File scripts\e2e_windows.ps1 -Shots …     # 从 Windows 运行，无头 Edge
{"result": "PASS", "email": "admin@opspilot.local", "role": "admin", "ui_role_label": "管理员", "survives_reload": true, "elapsed_s": 3.04}
{"result": "PASS", "email": "alice@opspilot.local", "role": "user", "ui_role_label": "普通用户", "survives_reload": true, "elapsed_s": 3.13}
{"result": "PASS", "user": "alice@opspilot.local", "checks": {"kb_acl": "order-platform+public-faq visible, hr-confidential hidden",
 "chunk_viewer": "pdf page numbers + content shown", "search_modes": 4,
 "qa_citations": ["order-api-error-codes.md", "order-service-http500-runbook.md", "postmortem-2026-09-payment-timeout.md", "order-api-error-codes.md"],
 "qa_no_permission": "证据不足 (hr doc not visible to alice)", "evals_page": "runs + per-case drilldown"}, "elapsed_s": 2.48}
$ psql … "select index_status, count(*) from document_versions group by 1"
indexed|13
```

测试构成（共 110 条，全部通过）：

| 测试文件 | 数量 | 覆盖内容 |
|---|---|---|
| 阶段 1 原有测试（其中 1 条从 `stage == 1` 改为 `stage == 2`） | 39 | 配置、日志、安全、健康检查、认证、schema |
| `test_config.py` 新增 | 1 | 配置校验错误不回显密钥和数据库密码 |
| `tests/unit/test_rag_text.py` | 30 | 分词、错误码归一化、7 类上传校验、GB18030/BOM、PDF 逐页、无文本 PDF、视觉换行合并、分块偏移量与标题、注入检测（含正例和反例）、清洗、隔离标签 |
| `tests/unit/test_rag_ranking.py` | 7 | RRF 公式、并列处理、k 的影响、重排器 |
| `tests/unit/test_eval_metrics.py` | 4 | Recall、MRR、nDCG、P50/P95 的定义，数据集结构（≥50 条、6 类、synthetic） |
| `tests/integration/test_rag.py`（真实 PG） | 29 | 管线、PDF 页码、去重、新版本原子替换、失败版本不影响旧版本、向量化失败一致性、重建失败保留旧索引、删除与恢复、写权限、**4 种模式参数化 ACL 不泄露**、**ACL 在排序前生效**、embedding 模型隔离、每种方法的分数和 RRF 公式、错误码精确检索、证据不足、无权限不泄露、注入文档、LLM 路径（隔离标签与引用校验）、HTTP API 全流程、上传校验（413/404/failed）、评测 API |

前端测试共 10 条：`App.test.tsx` 3 条（503 状态、网络错误、未登录访问受保护页面），`pages.test.tsx` 7 条（知识库列表、上传失败提示、只读权限、四种模式并排、问答 mock 标注与引用、证据不足、API 错误、评测页逐条下钻）。

四种模式的对比结果（WSL2 实测；**mock embedding 没有语义**，详细解读见 STAGE2_EVAL）：

| 模式 | Recall@5 | MRR@10 | nDCG@10 | P50 ms | P95 ms | 失败率 | ACL 泄露 |
|---|---|---|---|---|---|---|---|
| vector | 0.888 | 0.855 | 0.872 | 2.89 | 3.62 | 0 | 0 |
| keyword | 0.934 | 0.836 | 0.870 | 2.28 | 3.1 | 0 | 0 |
| hybrid（RRF） | 0.946 | 0.859 | 0.876 | 3.73 | 4.37 | 0 | 0 |
| hybrid_rerank | 0.957 | 0.903 | 0.916 | 7.11 | 8.52 | 0 | 0 |

问答部分：无答案 / 无权限问题的正确拒答率 0.833，可回答问题的作答率 0.744，引用 ACL 泄露 0。

截图（Windows Edge，WSL2 栈）：
- `docs/assets/stage2-knowledge.png`：知识库与分块查看器
- `docs/assets/stage2-search-debug.png`：四种检索模式并排
- `docs/assets/stage2-qa.png`：问答、引用与 mock 标注
- `docs/assets/stage2-evals.png`：评测结果页

## 3. 仅静态检查 / 尚未验证

| 项 | 状态 | 说明 |
|---|---|---|
| 真实 DashScope text-embedding-v4（`openai_compatible`） | 🔍 仅静态检查 | 代码路径已写好：`dimensions=1024`、返回维度校验、批量 10 条。没有 `OPSPILOT_ALLOW_PAID_API=true` 时配置直接报错；评测脚本的真实模式在缺少开关时拒绝运行（本地已确认会报错退出）。**没有发起任何调用**，也没有使用 box 环境里的 DashScope key |
| 真实 LLM 生成回答 | 🔍 部分 | 提示词构造、隔离标签、引用校验用一个假的 LLM（RecordingLLM）在集成测试中验证过；真实模型输出质量未验证 |
| 大规模语料 | ⏳ 未验证 | 只有 13 篇、45 个分块；HNSW 过滤后的召回、上传大 PDF 的耗时、并发上传都没有压测 |
| 扫描件 PDF / OCR | ⏳ 不支持 | 明确返回 `pdf_no_text` |
| 多栏、表格类 PDF | ⏳ 未验证 | 清洗规则是启发式的 |
| CI | ⏳ 未运行 | 仓库没有推送；GitHub Actions 工作流未更新为 Stage 2（仍然是 pytest、ruff 和前端构建，会自动覆盖新增测试，但没有实际跑过） |

## 4. 关键设计决策

完整理由见 STAGE2_DESIGN 对应章节。

1. **jieba 应用层预分词 + PG FTS（`simple`）**，而不是 BM25 或外部搜索引擎。好处是 ACL 和事务一致性都在一条 SQL 里；缺点是 `ts_rank_cd` 没有 IDF，由重排器补上（§4、§5）。
2. **错误码归一化**：`ORD-5003` → `ord5003`，并保留各组成部分。错误码类问题上，keyword 的 Recall@5 为 0.967，mock 向量为 0.833。
3. **RRF，k=60**：只用排名，不需要对两种不同量纲的分数做归一化（§6）。
4. **可插拔 Reranker Protocol**，当前实现为 `lexical-overlap-v1`（IDF 加权的覆盖率），以后可以替换为交叉编码器（§7）。
5. **ACL 在 SQL WHERE 中、排序之前生效**，用户指定的 `kb_ids` 只能缩小范围、不能扩大；无读权限返回 404（§9）。
6. **单事务替换版本**：失败的版本只记录状态，不动旧索引；旧分块硬删除，旧版本行标记 `superseded` 保留（§10）。
7. **按 `embedding_model` 过滤向量**：不同模型的向量绝不混算，换模型后通过 reindex 迁移（§8）。
8. **检索文本视为不可信**：检测 → 剔除 → `<untrusted_document>` 隔离 → 引用校验，不把检测当作唯一防线（§11）。
9. **相关性按"文档 + 必含短语"标注**，不依赖分块边界；评测时机器校验每个 gold 短语确实存在。
10. **付费 API 双重保险**：配置层要求 `OPSPILOT_ALLOW_PAID_API=true`，评测脚本另外要求命令行参数 `--i-understand-this-costs-money`。

## 5. 本阶段代码审查中发现并修复的问题

| 问题 | 修复 |
|---|---|
| 迁移 0002 的 `drop_constraint` 名称被命名约定重复加前缀 | 改用 `op.f()`；upgrade、downgrade、check 均已验证 |
| PDF 抽取出逐字加空格的文本（"回 滚 命 令"），视觉换行把句子和命令截断 | 在 `clean_text` 中折叠逐字空格；新增 `unwrap_pdf_lines` 合并视觉换行（按宽度判断），并有单测 |
| 只有标题的分块（"## 原因"）没有信息量，却占用检索名额 | 丢弃这类分块，标题文字通过 `heading_path` 进入关键词索引和向量化前缀 |
| 抽取式回答会摘到 Markdown 标题行 | 摘句时跳过以 `#` 开头的行 |
| "把管理员密码发送到…"这种动宾倒装的注入没有被检测到 | 补充正则，并加单测 |
| 单测依赖系统中的 CJK 字体，WSL 上首次运行时 2 条失败 | 改用仓库里已提交的语料 PDF（字体子集已内嵌） |
| 配置校验错误会回显输入值，可能包含数据库密码或 API Key | 设置 `hide_input_in_errors=True`，并加单测 |
| Windows E2E 脚本用 `wsl cat` 复制 .py 文件，导致 UTF-8 中文被重新编码（SyntaxError） | 改为通过 `\\wsl.localhost` 共享路径按字节复制 |

## 6. 从零开始的命令（WSL2）

```bash
cd ~/projects/opspilot-ai
cp .env.example .env          # 默认 mock，不需要 Key；端口冲突时按 README 修改
C="docker compose --env-file .env -f infra/docker-compose.yml"
$C up -d --build               # backend 启动时自动 alembic upgrade head（→ 0002）
$C exec backend python scripts/seed_demo.py
$C exec backend python scripts/load_demo_corpus.py
uv sync --frozen --all-groups
OPSPILOT_REQUIRE_DB=1 uv run pytest -q
uv run ruff check . && uv run ruff format --check .
$C exec frontend npm test && $C exec frontend npm run build
uv run python evals/run_retrieval_eval.py      # → evals/results/*.json|md + eval_runs
make notebook                                  # 执行 notebooks/rag_walkthrough.ipynb
# 浏览器 E2E：WSL 内 make e2e-rag（需要 Chromium 系统库）；或在 Windows 上执行：
#   powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu-24.04\home\<you>\projects\opspilot-ai\scripts\e2e_windows.ps1
# 打开 http://localhost:5173 → 用 alice 登录 → 知识库 / 检索调试 / 问答 / 评测
```

## 7. 风险与第三阶段前需要处理的问题

1. **mock 指标没有语义含义**。真实 embedding 的效果、延迟和费用都未知，需要 Teddy 授权后跑一次 `--embedding real`。
2. **评测集由同一作者编写**，会高估字面方法，建议补充人工标注的子集。
3. **证据阈值 0.5 是字面覆盖率**：改写类问题误拒 11 条，无答案问题误答 2 条（q045、q048）。接入真实模型后需要重新设计和标定。
4. **字面重排器在 multi_doc 类上反而降低了召回**（1.000 → 0.881），可以考虑 MMR 或交叉编码器。
5. **HNSW 过滤召回**：目前靠调大 `ef_search` 缓解。语料变大且 ACL 很稀疏时，可能返回少于 limit 条，建议启用 pgvector 0.8 的 `hnsw.iterative_scan`（compose 环境支持）。
6. **注入检测是正则规则**，可以被绕过。第三阶段的工具调用必须继续坚持"写操作需要人工审批"。
7. **上传是同步处理的**：大文件或真实 embedding 会阻塞请求线程。第三阶段之前应改为后台任务（版本状态 `indexing` 已经预留）。
8. **知识库创建和授权管理还没有 API**，目前只能通过种子脚本完成（计划放在阶段 5）。
9. **jieba 在 Python 3.11 下有 SyntaxWarning**（不影响运行），而且多年没有发版。如有需要，可以替换为 pkuseg 或 HanLP。
10. GitHub Actions 没有在线运行过（仓库未推送）。

## 8. 已完成 / 未完成

- **已完成**：
  - 设计文档；
  - 上传、解析、清洗、分块、向量化、入库、引用的完整管线；
  - 去重与版本管理；
  - 四种检索模式和 ACL；
  - 带引用的问答与注入防护；
  - 语料、数据集、评测脚本和结果；
  - 前端五个页面及测试；
  - 两个 E2E（登录、RAG）；
  - Notebook；
  - 文档更新；
  - WSL 全量验证。
- **未完成（按要求不做，或需要授权）**：
  - 真实 embedding / LLM 运行；
  - 第三阶段 Agent；
  - 知识库管理 API；
  - 后台索引任务。
