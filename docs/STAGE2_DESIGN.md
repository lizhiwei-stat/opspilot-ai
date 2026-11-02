# 第二阶段技术设计：企业知识库 RAG

> 范围：文档上传 → 解析 → 清洗 → 分块 → 向量化 → 入库 → 检索（向量 / 关键词 / 混合 RRF / 重排）→ 带引用的问答 → 离线评测 → 前端管理与调试页面。
> **不在本阶段**：Agent 编排、工具调用、工单（第三、四阶段）。
> 默认 **mock 模式**：mock embedding + mock 抽取式回答，不调用任何付费 API。真实 DashScope 路径已实现，但需要显式开关，本阶段**没有运行**。

## 0. 验收标准（逐条可验证）

| # | 验收项 | 判定方式 |
|---|---|---|
| A1 | 支持 PDF / Markdown / TXT 上传；拒绝其他类型、空文件、超限文件、扩展名与内容不符、二进制伪装文本 | `tests/unit/test_rag_text.py::test_validation_errors`、`tests/integration/test_rag.py::test_api_upload_validation` |
| A2 | PDF 逐页解析，分块带页码；Markdown 分块带标题路径；分块偏移量精确（`content == 原文[start:end]`） | `test_pdf_per_page`、`test_chunk_offsets_exact_and_headings`、`test_pipeline_pdf_has_page_numbers` |
| A3 | 内容 SHA-256 去重（同一知识库内） | `test_content_hash_dedup` |
| A4 | 同名文件再次上传 = 新版本；旧版本标记 `superseded`，旧分块在**同一事务**内删除；失败的新版本不影响旧版本继续服务 | `test_new_version_replaces_old_chunks_atomically`、`test_failed_new_version_keeps_serving_old`、`test_version_rows_consistent` |
| A5 | 删除 / 重建索引；向量化失败时数据库状态一致（无半截分块） | `test_delete_removes_chunks_and_reupload_revives`、`test_embedding_failure_is_recorded_and_consistent`、`test_reindex_failure_keeps_old_index` |
| A6 | 解析错误有明确错误码和中文信息，文档版本状态可见（`indexing/indexed/failed/superseded`） | API `latest_error`，前端状态徽标 |
| A7 | ACL 在 SQL `WHERE` 中、**排序之前**生效；任何模式下都不泄露无权限文档 | `test_acl_no_leak_in_any_mode`（4 模式参数化）、`test_acl_filter_is_applied_before_ranking`，评测 ACL 泄露 = 0 |
| A8 | 统一检索接口返回 4 种模式结果，带每种方法的分数和排名 | `test_modes_return_per_method_scores`，前端检索调试页 |
| A9 | 回答引用真实 chunk id、文档、页码；证据不足时固定回答"证据不足" | `test_qa_grounded_mock_with_real_citations`、`test_qa_insufficient_evidence`、`test_qa_no_permission_is_insufficient_not_leak` |
| A10 | 检索内容视为不可信：注入行被标记并剔除；真实 LLM 提示中用 `<untrusted_document>` 包裹并校验引用 | `test_injection_doc_is_flagged_and_stripped`、`test_llm_path_wraps_untrusted_and_validates_citations` |
| A11 | Mock 回答明确标注 `generator=mock-extractive`、`is_mock=true`，界面有醒目提示 | 前端测试 `QA page`，e2e |
| A12 | ≥50 条中文评测问题，覆盖改写、错误码、精确关键词、多文档、无答案、无权限；标注来源诚实（synthetic） | `tests/unit/test_eval_metrics.py::test_dataset_shape` |
| A13 | 评测脚本可复现，输出 JSON+MD（含 commit、provider、时间）并写入 `eval_runs`，前端可读取 | `evals/run_retrieval_eval.py`，`/api/v1/evals/runs` |
| A14 | Notebook 中文、执行后保留输出 | `notebooks/rag_walkthrough.ipynb` |
| A15 | WSL compose 栈上 pytest / ruff / 前端测试与构建 / e2e / notebook / 评测全部真实运行 | `docs/reports/STAGE2_REPORT.md` |

## 1. 总体流程

```
上传(multipart) ─► 校验(类型/大小/魔数/NUL/编码) ─► 计算 sha256 ─► 同 KB 已有相同内容? ──是─► duplicate
                                                              │否
                       同名文档? ─否─► 新建 Document ─┐        ▼
                                  是─► 复用 Document ─┴► 新 DocumentVersion(indexing)
                                                              │
     解析(PDF 逐页 / MD / TXT) ─► 清洗 ─► 分块(标题感知 + 中英文分隔符) ─► 分词(jieba) ─► 向量化(批量)
                                                              │任何一步失败 → version=failed, "[code] 信息"，旧版本不受影响
                                                              ▼
           单一事务：插入新分块 → 删除该文档其它版本分块 → 旧版本 superseded → 新版本 indexed → current_version
```

检索：`query → jieba 分词 ─┬─► 关键词：PG FTS（ACL + is_active 过滤）
                          └─► 向量：pgvector cosine（ACL + is_active + embedding_model 过滤）
          → RRF 融合（k=60）→ [可选] 重排（Top-20 候选）→ Top-K（带每种方法的分数/排名）`

问答：`hybrid_rerank 检索 → 清洗注入行 → 证据覆盖率 ≥ 阈值的分块（最多 4 个）→ mock 抽取式 / LLM 生成 → 引用校验`

## 2. 解析与清洗

- **白名单**：`.pdf .md .markdown .txt`。文件名只取 basename（防路径穿越），长度截断。
- **魔数校验**：`.pdf` 必须以 `%PDF-` 开头；文本文件若以 `%PDF-` 开头也拒绝（伪装）；文本中含 NUL 视为二进制。
- **编码**：先 `utf-8-sig`（兼容 BOM），失败再 `gb18030`（覆盖 GBK/GB2312，国内 Windows 常见），都失败则报 `decode_failed`。
- **PDF**：`pypdf` 逐页抽取；加密 → `pdf_encrypted`；损坏 → `pdf_corrupt`；全部页面无文本（扫描件）→ `pdf_no_text`（不做 OCR，明确告知）。
- **清洗**：统一换行、去控制字符、NFC 规范化、去行尾空白、压缩 3 个以上空行。
  - PDF 特有：部分生成器逐字定位，抽取结果形如"回 滚 命 令"，按"单字符 token 占比 ≥80%"判定后折叠；
  - 视觉换行合并：上一行"足够长"（≥ 页面最长行宽度 70%，中文按 2 倍宽度计）且不以句末标点结束、下一行不是列表/标题时，合并为同一段。
  - 局限：启发式规则，复杂版式（多栏、表格）仍可能错序，已在报告"风险"中列出。

## 3. 分块参数与理由

- **算法**：先按 Markdown 标题切节（记录 `heading_path`，如 `订单服务手册 > 2. 常见原因 > 2.2 连接池耗尽`），节内用 LangChain `RecursiveCharacterTextSplitter`。
- **分隔符优先级**：`\n\n` → `\n` → `。！？；` → `. ! ? ;`（带空格）→ `，` `, ` → 空格 → 字符。中文没有空格分词，必须把中文标点放在空格之前，否则会在句中切断。
- **chunk_size = 500 字符，overlap = 80（16%）**：
  - 技术支持文档的一个"知识单元"（一个错误码条目、一个排查步骤）通常 100–400 个汉字；500 能完整容纳一个小节，又不会把多个无关小节揉在一起稀释向量。
  - text-embedding-v4 单条输入上限 8192 token，500 汉字约 300–500 token，远低于上限，留出标题前缀空间。
  - overlap 80 约等于 1–2 句中文，保证跨块边界的句子在两侧至少一侧完整。
  - 可通过 `OPSPILOT_CHUNK_SIZE / OPSPILOT_CHUNK_OVERLAP` 调整（校验 overlap < size）。本阶段**没有**做参数扫描实验，这是后续工作。
- **长度单位是字符**而非 token：与具体模型 tokenizer 解耦；`token_count` 字段目前存字符数（命名沿用第一阶段表结构，文档中注明）。
- **偏移量**：`char_start/char_end` 相对于 PDF 当页文本或文本文件全文，满足 `content == text[start:end]`（代码中断言 + 单测），前端可据此高亮原文。
- **标题单独成块**会被丢弃（没有信息量），标题文字通过 `heading_path` 进入关键词索引和向量化前缀。
- **向量化输入**：`"{文档标题} > {heading_path}\n{content}"`——给向量补充上下文，展示和引用仍用原始 `content`。

## 4. 分词：为什么用 jieba，以及局限

PostgreSQL 内置的 `simple`/`english` 解析器按空白和标点切词，中文整句会变成一个 token，完全无法检索。可选方案：

| 方案 | 优点 | 缺点 |
|---|---|---|
| zhparser / pg_jieba 扩展 | 数据库内分词 | 需要编译安装扩展（官方 pgvector 镜像没有），部署复杂 |
| pg_trgm 三元组 | 无需分词 | 中文 3-gram 噪声大，排序质量差，索引体积大 |
| **应用层 jieba 预分词 + `to_tsvector('simple', …)`** | 纯 Python、无扩展、索引和查询走同一函数保证对称 | 分词在应用层，需要保持写入/查询一致 |

选择 jieba（搜索模式 `cut_for_search`，会额外输出子词，如"数据库"→"数据/据库/数据库"，提高召回），加载领域词典 `rag/data/userdict.txt`（连接池、熔断器、灰度发布……）。

**代码标识符归一化**：`ERR_PAY_TIMEOUT`、`ORD-5003`、`PaymentAdapter.charge` 在 PG 解析器中会被 `_ - .` 拆散。我们在 jieba 之前用正则识别，整体保留为 `errpaytimeout`、`ord5003`，同时保留组成部分 `err/pay/timeout`，精确查询和部分查询都能命中。

**局限**：
- jieba 词典是通用领域的，新词/产品名需维护 userdict；
- 搜索模式会产生较多子词，可能轻微降低精确率；
- 停用词表是手写的小表；
- 不做同义词扩展（"回滚" ≠ "退回旧版本"），这正是需要真实向量模型的地方。

## 5. 关键词检索：BM25 还是 PG 全文检索

| | BM25（如 Elasticsearch / rank_bm25 / ParadeDB pg_search） | PG FTS（`tsvector` + GIN + `ts_rank_cd`） |
|---|---|---|
| 排序质量 | 有 IDF 和词频饱和，公认更好 | `ts_rank_cd` 只看覆盖密度和长度归一化，**没有 IDF**，不是 BM25 |
| ACL 预过滤 | 外部引擎需要同步权限字段，存在不一致窗口 | 与向量检索同一条 SQL 的 `WHERE`，天然一致 |
| 事务一致性 | 需要双写/异步同步，删除后可能短暂可见 | 分块插入/删除与索引同一事务 |
| 运维 | 额外服务或扩展 | 零新增组件 |

**结论**：本阶段用 PG FTS。理由是 ACL 与事务一致性优先于排序上的边际收益，而且最终排序由 RRF + 重排决定，关键词通道只需"把含关键词的块召回到候选集"。
- 查询：分词后用 OR（`a | b | c`）组成 `to_tsquery('simple', …)`，再用 `ts_rank_cd(tsv, q, 1)`（除以 1+log(文档长度)）排序。
- 实现细节：`chunks.tsv` 是 `GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED`，配 GIN 索引；`search_text` 是空格连接的 jieba 分词结果（含 `heading_path`）。
- 局限：没有 IDF，常见词（"订单"）和稀有词（"ord5003"）权重相同。重排器用 IDF 补上这一点（§7）。
- 如果以后需要真正的 BM25，可以换 ParadeDB `pg_search`，它同样在 PG 内、可共享 WHERE。

## 6. 向量检索与 RRF

- **向量检索**：pgvector `vector(1024)`，HNSW（`vector_cosine_ops`），`1 - cosine_distance` 作为分数。过滤条件包括 ACL、`is_active`，以及 **`document_versions.embedding_model = 当前模型`**（§8）。
  - 由于 HNSW 是先近似搜索再过滤，事务内把 `hnsw.ef_search` 设为 `max(40, 4×limit)`，减少过滤后结果不足的情况。
  - pgvector 0.8 的 `hnsw.iterative_scan` 可以彻底解决这个问题，但本地备用环境是 0.6，暂未启用（列入风险）。
- **RRF（Reciprocal Rank Fusion，Cormack et al. 2009）**：

  $$\mathrm{RRF}(d) = \sum_{m \in \{\text{vector},\,\text{keyword}\}} \frac{1}{k + \mathrm{rank}_m(d)}$$

  - rank 从 1 开始；某通道没召回的文档在该通道贡献为 0。
  - 分数相同时按首次出现顺序，结果确定。
- **k = 60**：沿用原论文和 Elasticsearch / OpenSearch 的默认值。k 越小越偏向"某一路排第一"，k 越大越偏向"两路都出现"（单测 `test_rrf_k_small_favours_head_large_favours_consensus` 验证了这一点）。
  - RRF 只用排名、不用原始分数，因此不需要对余弦相似度和 `ts_rank_cd` 这两种量纲完全不同的分数做归一化。这是选它而不是加权求和的主要原因。
- 每路取 `retrieval_candidates = 50` 个候选参与融合。

## 7. 重排器接口

```python
class Reranker(Protocol):
    name: str
    def score(self, query: str, passages: list[str]) -> list[float]: ...
```

- 与 embedding 的区别：embedding 是双塔（bi-encoder），查询和文档**分别**编码；重排器对（查询, 段落）**成对**打分，能利用二者的交互。
- 本阶段实现 `LexicalOverlapReranker`（`lexical-overlap-v1`），纯 CPU、确定性：
  - 分数 = 查询词在段落中出现部分的 IDF 权重之和 ÷ 全部查询词 IDF 权重之和（IDF 在候选集内统计），再加上错误码精确命中的小额奖励（≤0.1），截断到 [0, 1]；
  - 它是**字面信号**，理解不了改写。之所以有用，是因为它补上了 `ts_rank_cd` 缺少的 IDF，并且看的是融合后的候选。
- 只对 RRF 后的 Top-20（`rerank_candidates`）重排，控制延迟。
- 可替换实现：交叉编码器（bge-reranker）或 DashScope `gte-rerank`。实现同一 Protocol 即可，不改调用方。

## 8. Embedding 提供方、维度兼容与重建索引

- 列类型固定为 `vector(1024)`。配置校验强制 `embedding_dim == 1024`，换维度必须写迁移，不能静默截断。
- text-embedding-v4 支持通过 `dimensions` 参数输出 1024 维，`OpenAICompatEmbedding` 显式传入 `dimensions=1024` 并校验返回长度。
- **模型标识**：每个版本记录 `document_versions.embedding_model`。向量检索只比较与当前查询模型一致的向量，**不同模型的向量绝不混算**（`test_vector_search_ignores_other_embedding_models`）。
  - 切换模型后，旧向量在向量通道里"不可见"，关键词通道照常工作，服务降级但不会出错。
- **重建索引策略**：
  - `POST /documents/{id}/reindex` 按当前模型重新分块和向量化。新向量在内存中全部算好后，才在一个事务里删除旧分块、写入新分块；
  - 向量化失败时抛出 422，原索引保持不动（`test_reindex_failure_keeps_old_index`）。
- 全库切换步骤：
  1. 修改 `.env`（provider/model），并设置 `OPSPILOT_ALLOW_PAID_API=true`；
  2. 对所有文档执行 reindex（评测脚本 `--embedding real` 会自动做这一步）；
  3. 期间可以用 `count_stale_chunks` 查看进度。
- **付费保护**：任何非 mock 的 provider 都要求 `OPSPILOT_ALLOW_PAID_API=true` 且 API Key 非空，否则配置加载直接报错。评测脚本的真实模式还额外要求命令行参数 `--i-understand-this-costs-money`。

## 9. ACL 预过滤

可读知识库集合 `accessible_kb_ids(user)`：
- 管理员：全部知识库；
- 其他用户：`visibility='public'`，或自己是 owner，或在 `kb_permissions` 中有授权。

检索 SQL 的 `WHERE` 统一包含：

```sql
chunks.kb_id IN (<accessible_kb_ids 子查询>)
AND chunks.is_active AND documents.status = 'active'
[AND chunks.kb_id IN (:kb_ids)]          -- 用户指定的范围只能缩小、不能扩大
[AND document_versions.embedding_model = :model]   -- 仅向量通道
```

- 过滤发生在 `ORDER BY … LIMIT` **之前**。`test_acl_filter_is_applied_before_ranking` 用 `limit=1` 证明：如果是后过滤，bob 会拿到空结果；实际上他拿到的是自己可见的最相关块。
- 文档详情、分块列表、上传、重建、删除都先做同样的权限判断。无读权限时返回 404（不暴露资源是否存在），有读无写时返回 403。
- 问答只基于已经过 ACL 的检索结果，因此"无权限"问题的表现与"无答案"相同，都是"证据不足"，不会泄露受限文档里有答案这一事实。

## 10. 数据库与索引一致性

- 上传和重建都在单个请求的工作单元（`get_db`：成功提交，异常回滚）中完成：
  - 新分块插入、旧分块删除（`replace_document_chunks`）、旧版本标记 `superseded`、`current_version` 更新在**同一事务**内；
  - 检索要么看到完整的旧版本，要么看到完整的新版本。
- 失败路径：
  - 解析或向量化失败时，新版本记为 `failed`，带 `[code] 信息`，事务正常提交，失败记录可见；
  - 此时没有写入任何分块，旧版本照常服务。
- 原文件按 `upload_dir/<kb_id>/<sha256><ext>` 存储（内容寻址，先写临时文件再原子 rename），reindex 从这里读取。
- 同一文档的并发上传：`SELECT … FOR UPDATE` 锁住 Document 行，`(document_id, version)` 有唯一约束兜底。
- 删除：文档软删除（`status='deleted'`，保留版本历史用于审计），分块硬删除（释放向量索引空间）。同名文件再次上传会恢复该文档。
- 关键词索引（生成列 `tsv`）和向量都在 `chunks` 行内，不存在跨存储不一致的问题。
- 迁移 0002 新增 `ix_chunks_document_version_id`、`ix_chunks_document_id`（支撑按版本/文档批量删除），并把 `superseded` 加入状态 CHECK 约束。

## 11. 提示注入处理

检索到的文本一律视为**不可信数据**：

1. **检测**：用正则启发式匹配：
   - "忽略之前的指令"、"你现在是"、"system prompt"；
   - 伪造的 `<system>` / `</untrusted_document>` 标签；
   - "把密码发送到…"；
   - "直接关闭工单 / 调用工具："。
   这些规则写在 `rag/sanitize.py` 中，并有正例和反例单测（防止把"忘记密码怎么办"这类正常文本误判）。
2. **标记**：检索结果带 `suspicious` 标志，前端显示"可疑内容"。
3. **剔除**：生成回答前逐行删除命中规则的行，回答和引用摘录中都不会出现这些内容。问答结果返回 `flagged_chunks`，前端给出警告。
4. **隔离**（真实 LLM 路径）：
   - 每个文档包在 `<untrusted_document id=… source=…>` 中，内容里的尖括号先替换为全角形式，防止伪造结束标签；
   - 系统提示词声明文档中的任何指令都必须忽略。
5. **引用校验**：模型输出中的 `[n]` 必须对应本次提供的文档编号，不存在的编号会被丢弃。没有任何有效引用时回答"证据不足"（`test_llm_without_valid_citation_becomes_insufficient`）。
6. **局限**：正则检测可以被改写绕过，比如同义改写或其他语言。真正的防线是第 4、5 点，以及第三、四阶段的"工具调用需人工审批"，不依赖检测本身。

## 12. 证据阈值与"证据不足"

- 证据分数 = 查询词（jieba 分词、去停用词、去重）在分块清洗后文本中的覆盖比例 ∈ [0, 1]。
- 选出覆盖率 ≥ `qa_min_evidence = 0.5` 的分块，最多 4 个。一个都没有时回答固定文案：`证据不足：在你有权限访问的知识库中没有找到足以回答该问题的可靠依据。`
- 0.5 的含义是"问题中至少一半的实词在同一个分块里出现过"。在评测集上：
  - 无答案 / 无权限问题的正确拒答率为 0.833；
  - 可回答问题的作答率约 0.74；
  - 即存在误拒和误答，见评测报告。
- 局限：这是字面覆盖率，换了说法的问题容易被误拒。接入真实模型后，应改为相似度阈值或重排分数阈值，并在评测集上重新标定。

## 13. Mock 与真实的标注

| 位置 | 标注 |
|---|---|
| `/api/v1/system/version` | `embedding_provider`, `llm_provider`, `rag.reranker` |
| 检索结果 | `embedding_model = mock-hash-embedding-v1` |
| 问答结果 | `generator = mock-extractive`，`is_mock = true`，回答首行固定 `【Mock 抽取式回答 · 未调用大模型，以下内容为原文摘录】` |
| 前端 | 检索调试页、问答页、评测页均有黄色提示条："mock embedding 没有语义理解" |
| 评测结果 | `meta.embedding_has_semantics = false` + `warning` 文本，写入 JSON/MD/DB |

**mock embedding 是什么**：对单个汉字和英文单词做特征哈希（blake2b → 1024 维 ±1），再做 L2 归一化。它本质上是**字面重叠的相似度**，与 TF 向量的余弦相似度近似，**没有任何语义**。Notebook §4 演示：同义改写的余弦只有 0.156，字面相近的则有 0.898。

## 14. 评测设计

- 语料：13 篇**合成**中文技术支持文档（MD 7 / TXT 3 / PDF 3），分布在 3 个知识库：
  - `order-platform`：受限，alice 有读权限，bob 没有；
  - `public-faq`：公开，其中包含 1 篇带注入内容的"供应商备注"；
  - `hr-confidential`：受限、无授权，只有管理员可读。
- 数据集 `evals/datasets/retrieval_zh.jsonl`，55 条：

  | 类别 | 条数 |
  |---|---|
  | exact_keyword | 10 |
  | error_code | 10 |
  | paraphrase | 16 |
  | multi_doc | 7 |
  | no_answer | 6 |
  | no_permission | 6 |

  每条注明提问用户（权限视角）。
- **标注来源（诚实说明）**：`label_source = synthetic`。语料和问题由 AI 助手一并编写，**没有人工独立标注**。评测脚本运行时会机器校验每个 gold 短语确实存在于 gold 文档中，校验失败直接中止。由于出题人和写文档的是同一方，问题措辞与文档的字面重合度偏高，这会**高估**字面方法（关键词、mock 向量）的效果。
- **相关性定义**：gold 项 = (文档, 必含短语)。某个分块属于该文档且包含该短语即视为相关。这样定义不依赖分块边界，修改分块参数后标注依然有效。
- **指标**：
  - Recall@5：Top-5 覆盖的 gold 项比例；
  - MRR@10：第一个相关分块排名的倒数；
  - nDCG@10：二元相关性；
  - 延迟 P50/P95：单条查询端到端，含向量化，预热后测量；
  - 失败率：异常占比；
  - ACL 泄露：返回结果中属于用户无权知识库的分块数，按独立写定的权限矩阵核对；
  - 问答：无答案 / 无权限问题的正确拒答率、可回答问题的作答率、引用 ACL 泄露。
- **真实 embedding 评测路径**：`--embedding real --i-understand-this-costs-money`，并在 `.env` 中设置 provider、key 和 `OPSPILOT_ALLOW_PAID_API=true`。脚本会先用真实模型 reindex 全部文档，再评测。**本阶段未运行**，运行前需要 Teddy 授权。

## 15. 前端

- 原生 React + hash 路由，不新增依赖。五个页面：概览、知识库、检索调试、问答、评测。全部调用真实 API，未登录时引导登录。
- 知识库页：
  - 知识库列表（只显示可读的）；
  - 文档表格（状态、版本、分块数、错误信息）；
  - 上传、重建、删除（仅有写权限时显示）；
  - 版本历史（含 sha、embedding 模型）；
  - 分块查看器（页码、标题路径、偏移量、关键词索引 token）。
- 检索调试页：同一查询并排展示四种模式，显示分词结果、每种方法的分数和排名、各阶段耗时、可疑内容标记。
- 问答页：回答、生成器标签、证据分数与阈值、引用（文档 / 页码 / chunk id / 摘录）、注入警告、检索候选。
- 评测页：读取 `/api/v1/evals/runs`，展示指标表和逐条结果，并显示 mock 警告。
