# Stage 2.5 评测报告：真实本地 Embedding + 混合检索 + Reranker

所有数字来自 `evals/results/` 中已提交的 JSON（WSL 上真实运行，2026-11-02 13:00–13:30 HKT），汇总表由 `evals/summarize_stage25.py` 自动生成：[STAGE2_5_TABLES.md](STAGE2_5_TABLES.md)。本文只做解释，不手抄可能出错的数字以外的结论。**没有任何付费 API 调用。**

## 1. 环境与模型

| 项 | 值 |
|---|---|
| 机器 | WSL2 Ubuntu-24.04，16 核，15 GB RAM，**仅 CPU**（不依赖 GPU，未改动 ~/venvs/pytorch-rocm721） |
| 数据库 | PostgreSQL 16 + pgvector **0.8.7**（端口 5433） |
| 推理栈 | torch 2.x CPU 版 + sentence-transformers 5.x（`uv --group local-embed`，模型缓存在 WSL `~/.cache/huggingface`） |
| bge-small-zh-v1.5 | revision `7999e1d3`，512 维，CLS pooling，查询加官方指令前缀；加载 2.5s，88 chunks/s，查询 P50 4ms，峰值 RSS 712 MB |
| bge-m3 | revision `5617a9f6`，1024 维，2.2 GB；5.7 chunks/s，查询 P50 53ms，峰值 RSS 2.27 GB |
| bge-reranker-base（cross-encoder） | revision `2cfc18c9`；20 个候选约 1.7–2.0 s/查询（CPU） |
| Mock embedding | 基于哈希的 1024 维向量，**没有语义**，作为基线保留 |

基准数据：`evals/results/bench_local_models.json`（`scripts/bench_local_models.py`，8 线程）。

## 2. 数据集

- dev：`evals/datasets/retrieval_zh.jsonl` 55 条（Stage 2 起使用；阈值调参**只用 dev**）。
- held-out：`evals/heldout/retrieval_zh_heldout.jsonl` 37 条（≥30），**在任何调参之前冻结**，SHA-256 写入 `FROZEN.sha256`，评测前校验。冻结后唯一改动是 LC-001（h003 gold 引文与原文空格不一致 `down 迁移脚本`→`down迁移脚本`，属于标注拼写错误，发现时 held-out 从未被打分），见 `evals/heldout/LABEL_CHANGES.md`。没有为提高分数修改任何 gold。
- 题型：exact_id / paraphrase / multi_doc / no_answer / acl（越权用户查询）。

## 3. 指标

Recall@5、MRR@10、nDCG@10、P50/P95 延迟、失败率（异常/超时）、正确拒答率（no_answer 被拒）、作答率（应答题被回答）、ACL 隔离正确率（越权查询结果中无他人 KB 的 chunk）。

## 4. 检索结果（摘要，完整见 TABLES）

Recall@5（dev / held-out）：

| Embedding | vector | keyword | hybrid(RRF) | hybrid+rule | hybrid+cross-encoder |
|---|---|---|---|---|---|
| mock（无语义） | .888 / .944 | .934 / .963 | .946 / .944 | .957 / 1.000 | .969 / 1.000 |
| bge-small-zh | .891 / .889 | .934 / .963 | .954 / .926 | .988 / .963 | **1.000 / 1.000** |
| bge-m3 | **.950** / .926 | .934 / .963 | .957 / .926 | .965 / .963 | 未跑（CPU 时间） |

ACL 隔离正确率：所有配置 **1.000**，0 泄漏。失败率：所有配置 0。

### 解读（诚实版）

1. **语义检索确实帮到了改写类问题**：q022、q029（换说法提问）keyword 未命中、bge-small vector 命中；反过来 q002、q012（精确错误码/ID）vector 未命中、keyword 命中。hybrid(RRF) 同时拿回两类，这就是要做混合检索的原因。
2. **小语料上 mock 不一定输**：held-out 上 mock vector（.944）高于 bge-small vector（.889），bge-small vector 的 multi_doc 只有 0.600。语料只有 42 个 chunk，哈希向量对重叠字符串天然有利；不能据此说"语义模型无用"，但也说明本数据集规模不足以给出强结论。
3. **bge-m3 vector 明显强于 bge-small**（dev MRR .922 vs .810），代价是 5 倍延迟、3 倍内存。
4. **cross-encoder 是唯一在两套数据上都 Recall@5=1.0 的配置**，但 CPU 上每查询约 2 s，不适合默认在线路径；默认仍为 rule reranker，cross-encoder 作为可选项（`OPSPILOT_RERANKER=cross_encoder`）。
5. 越权 ACL 在 SQL 层过滤（KB 权限 JOIN 在向量排序之前），所以与 embedding 无关地保持 1.000。

## 5. 问答拒答（修复 a、b）

| 配置 | dev 正确拒答 / 作答 | held-out 正确拒答 / 作答 |
|---|---|---|
| Stage 2 基线（lexical≥0.5，无 anchor） | 0.833 / 0.744 | 0.700 / 0.667 |
| 当前 rule（lexical≥0.25 + anchor） | 1.000 / 1.000 | 0.600 / 0.963 |
| 当前 cross-encoder（rerank≥0.02 + anchor） | 1.000 / 1.000 | 0.500 / 1.000 |

- **(a) 11 个改写题误拒**（q021–q034）：逐条分析 `failure_category=below_threshold`，lexical 证据分 0.25–0.43，被 0.5 阈值误杀。阈值由 `evals/sweep_qa_threshold.py` 在 **dev** 上按事先声明的规则（先保证正确拒答率最高，再最大化作答率，取最保守的值）选到 0.25。
- **(b) q045 / q048 误答**（问 Java / Redis，知识库只有相近内容）：新增 "anchor 检查"——问题中的专有名词/技术词必须出现在证据中，否则拒答（`unmatched_anchor:Java`、`unmatched_anchor:Redis`）。
- **held-out 揭示过拟合**：dev 上 1.000，held-out 正确拒答率反而从 0.700 降到 0.600（rule）/0.500（CE）。误答 h029、h033、h036 等是"话题相近但知识库无答案"的问题，cross-encoder 也给出高分（h029 0.80、h036 0.84），**阈值本身无法区分**。看到 held-out 后**没有再调参**（否则 held-out 失去意义）。结论：需要可回答性判断（Stage 3 用 LLM 判定 + 引用校验），已写入 Stage 3 验收标准。
- 修复前后证据：`*_before_*`（基线）、`*_rule_*`、`*_local_*`、sweep 文件均已提交。

## 6. Reranker 修复（修复 c：multi_doc 下降）

根因：Stage 2 的 rule reranker 用单一词面分数**替换**融合排名，丢掉了向量+关键词的融合证据，且大量同分；q038、q039 的第二个 gold 文档在融合排名第 2、4，被重排挤出前 5。
修复：`OPSPILOT_RERANK_FUSION=rrf`，把重排名次与混合名次再做一次加权 RRF（w=0.5, k=60）。

| 数据 | 方案 | Recall@5 | MRR@10 | multi_doc |
|---|---|---|---|---|
| dev / bge-small / rule | replace → rrf | .957 → **.988** | .878 → **.909** | .881 → **1.000** |
| dev / mock / rule | replace → rrf | .957 → .957 | .903 → .878 | .881 → 1.000 |
| held-out / bge-small / CE | replace → rrf | 1.000 → 1.000 | 1.000 → .938 | 1.000 → 1.000 |

代价如实记录：mock dev 与 CE held-out 的 MRR 有所下降（RRF 把强 reranker 的第一名稀释了）。

另修复一个真实 bug：cross-encoder 被套了**两次 sigmoid**（sentence-transformers 传 `activation_fn=None` 会用模型默认 Sigmoid），所有分数挤在 0.5–0.73。改为 Identity + 我们自己的 sigmoid，加回归测试；修复前的结果文件 `20261102T045421Z_local_dev` 保留作证据。

## 7. pgvector 0.8 iterative scan（修复 d）

`scripts/exp_iterative_scan.py`：独立临时 schema `exp_iterscan`（结束即删除，不碰业务数据），10 万行、128 维、30 个查询、k=10、ef_search=40，按 ACL 分组过滤：

| 选择度 | 模式 | 平均返回行数 | 少于 k 的查询 | Recall vs 精确 | P50 ms |
|---|---|---|---|---|---|
| 1%（100 组） | off | **0.37** | 30/30 | 0.037 | 0.95 |
| 1% | relaxed_order | 10 | 0 | **0.72** | 7.73 |
| 1% | strict_order | 10 | 0 | 0.293 | 7.09 |
| 0.2%（500 组） | 三种模式 | 10 | 0 | 1.0 | 4.2–5.2 |

结论：带 ACL 过滤的 HNSW 在 off 模式下会"返回不足 k 条"（典型的过滤后召回塌陷），relaxed_order 最优。应用中启用 `hnsw.iterative_scan=relaxed_order` 并在外层按距离重新排序；pgvector < 0.8 时自动跳过。0.2% 一组三种模式都拿到精确结果，原因未深究（推测 planner/ef 与小分组的组合使结果集已完整），**未进一步验证**。本项目真实语料只有 42 个 chunk，线上不会触发该问题；实验用于证明设计。

## 8. 真实 Token 计数（修复 e）

`chunks.token_count` 原来是字符数，改名为 `char_count`（迁移 0003）；每个模型的真实 token 数存在 `chunk_embeddings.token_count`，由该模型自己的 tokenizer 计算（bge-small 全语料 4,757，bge-m3 3,879，字符数 6,323）。付费 API 则以返回的 `usage` 为准。

## 9. Prompt 注入防御（修复 f，超越正则）

分层：① 文档入库时正则/启发式标记可疑 chunk（Stage 2）② 检索内容一律包裹为不可信数据 ③ **输出策略检查** `rag/policy.py`：答案中出现的 URL 必须在白名单域（`opspilot.local`、`corp.local`）、不能出现系统提示泄漏/角色变更/越权指令、引用必须指向真实返回的 chunk ④ 查询侧注入（"忽略之前指令……"）不改变检索 ACL。测试使用一个**故意服从注入的假 LLM**（`tests/integration/test_stage25.py`），证明即使模型被攻破，策略层仍阻止输出——其中发现并修复了"无正则特征的注入链接被当作可信证据"的漏洞（新增 link_allowlist）。

## 10. 边界情况（修复 g）

已有测试：空查询/超长查询/纯符号、embedding 服务不可用 → 自动降级 keyword 并标记、维度不匹配（409）、模型间索引隔离、无权限 KB、空知识库、重复入库幂等。

## 11. 逐条错误分析

每个结果 JSON 的 `cases[]` 都含 检索侧 `failure_category`（ok / partial_multi_gold / ranked_low / right_doc_wrong_chunk / not_retrieved / n/a）与 QA 侧拒答原因（below_threshold / unmatched_anchor / policy 违规码）、gold、top-k、各信号分数；前端"评测"页按查询展示并带分类徽章。

## 12. 复现命令（WSL）

```bash
cd ~/projects/opspilot-ai
export HF_HUB_OFFLINE=1   # 模型已缓存后
uv run --group local-embed python scripts/bench_local_models.py
for e in mock local; do for d in dev heldout; do
  uv run --group local-embed python evals/run_retrieval_eval.py --embedding $e --dataset $d --rerankers rule,cross_encoder --tag ${e}_$d
done; done
OPSPILOT_LOCAL_EMBEDDING_MODEL=BAAI/bge-m3 OPSPILOT_LOCAL_EMBEDDING_REVISION=5617a9f61b028005a4858fdac845db406aefb181 \
  uv run --group local-embed python evals/run_retrieval_eval.py --embedding local --dataset dev --rerankers rule --tag m3_dev
uv run python scripts/exp_iterative_scan.py --n 100000 --selectivity 0.01
uv run python evals/summarize_stage25.py   # 重新生成 STAGE2_5_TABLES.md
```

## 13. 付费对照实验：百炼 text-embedding-v4（stage-2.5.1，已获批准并执行）

- 批准：Teddy，2026-11-02 13:49 HKT，approval id **`teddy-2026-11-02-t63s266`**（写入账本每一行）；按 [付费方案](../STAGE2_5_PAID_EMBED_PLAN.md) 执行，每次运行硬上限 ¥1（总额度 ¥10）。
- 配置：`text-embedding-v4`，1024 维，batch 10，OpenAI 兼容接口（北京地域）；独立索引（`embedding_models` 新行 + 独立部分 HNSW），未改动 mock / bge 向量。Key 仅在运行进程环境中注入，未写入任何文件；运行后对结果、账本、docs 做了按值比对的泄漏检查：0 个文件包含 Key。
- 运行时间：dev 13:44–13:50 HKT（411 s，含 cross-encoder），held-out 13:53–13:57 HKT（278 s）。
- **实际用量（来自 API 返回的 usage，账本 `evals/results/paid_ledger_teddy-2026-11-02-t63s266.jsonl`）**：473 次调用全部成功；输入 **9,272 tokens**（建索引 5 批 42 个 chunk 4,818 tokens + 468 次查询嵌入 4,454 tokens）；**实际费用 ¥0.004636**（dev ¥0.003678 + held-out ¥0.000958），远低于每次 ¥1 上限与预估上界 ¥0.0074。未触发任何预算拒绝。
- 调用次数比方案多（评测对每个模式、QA 各自重新嵌入查询，未做缓存），但 token 总量仍在预估内。

| 数据 | 模式 | text-embedding-v4 R@5 / MRR | bge-small | bge-m3 | mock |
|---|---|---|---|---|---|
| dev | vector | .872 / **.924** | .891 / .810 | .950 / .922 | .888 / .855 |
| dev | hybrid | .954 / **.942** | .954 / .893 | .957 / .929 | .946 / .859 |
| dev | hybrid+rule | .977 / .913 | .988 / .909 | .965 / .909 | .957 / .878 |
| dev | hybrid+CE | .977 / .965 | 1.000 / .965 | — | .969 / .942 |
| held-out | vector | .907 / .842 | .889 / .796 | .926 / .883 | .944 / .836 |
| held-out | hybrid | .944 / .883 | .926 / .869 | .926 / .926 | .944 / .866 |
| held-out | hybrid+rule | .963 / .935 | .963 / .901 | .963 / .963 | 1.000 / .914 |
| held-out | hybrid+CE | 1.000 / .957 | 1.000 / .938 | — | 1.000 / .938 |

keyword 在所有行相同（dev .934/.836，held-out .963/.867）；ACL 隔离 1.000、失败率 0。

**发现**
1. text-embedding-v4 向量检索**排序质量好（dev MRR .924，与 bge-m3 相当），但 Recall@5 不高**：multi_doc 只有 .691（dev）/.700（held-out），error_code 类 .817（dev）——第二个相关文档和精确错误码容易掉出前 5。
2. 混合检索后差距基本消失：hybrid dev MRR .942 为所有 hybrid 中最高；加 reranker 后与本地模型持平，没有显著优势。
3. 延迟：查询向量化走网络，vector P50 约 620–780 ms、P95 1.7–1.8 s，是本地 bge-small（12 ms）的 50 倍以上；且有外部依赖与费用。
4. 问答拒答与本地模型完全相同（held-out 0.500/1.000，误答 h029/h032/h033/h035/h036）——拒答问题与 embedding 无关，再次说明需要可回答性判断。
5. 结论：在本语料上**付费 embedding 不值得替换默认的本地 bge-small + hybrid + rule**；若追求单路向量质量，免费的 bge-m3 已达到同等 MRR。注意语料仅 42 chunk，结论只适用于本数据集。
