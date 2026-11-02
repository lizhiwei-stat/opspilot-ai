# ruff: noqa
"""Build notebooks/real_embedding_hybrid.ipynb (Chinese walkthrough). Execute with nbconvert."""
import json
from pathlib import Path

cells = []
def md(s): cells.append({"cell_type": "markdown", "metadata": {}, "source": s.strip()})
def code(s): cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": s.strip()})

md("""# Stage 2.5：真实本地 Embedding、混合检索与 Reranker（中文讲解）

本 Notebook 全部使用**免费的本地 CPU 模型**（bge-small-zh-v1.5、bge-reranker-base），不调用任何付费 API。
内容：
1. 语义检索 vs 关键词检索：为什么改写问题需要向量
2. RRF 融合：为什么混合检索能同时拿回两类问题
3. Cross-encoder 重排与"双 sigmoid" bug
4. 读取已提交的真实评测结果：Mock vs 真实 Embedding 并排
5. held-out 揭示的阈值过拟合
6. pgvector iterative scan 实验结果""")
code("""import json, glob, math, os, sys, time
from pathlib import Path
ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(ROOT / "backend" / "src"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
from opspilot.core.config import Settings
from opspilot.llm.local import LocalSentenceEmbedding, CrossEncoderReranker
from opspilot.rag.fusion import rrf
s = Settings()
print("embedding:", s.local_embedding_model, s.local_embedding_revision[:8])
print("reranker :", s.reranker_model, s.reranker_revision[:8])""")
md("""## 1. 语义检索 vs 关键词检索

向量模型把句子映射到同一个语义空间，"换个说法"的问题与原文的余弦相似度依然高；关键词检索只看字面重叠。
下面用 4 段运维文档片段做演示（片段为示意文本，与评测语料风格一致）。""")
code("""emb = LocalSentenceEmbedding(model_id=s.local_embedding_model, revision=s.local_embedding_revision,
                             query_instruction=s.local_query_instruction, threads=s.local_threads)
docs = [
    "数据库连接池耗尽时，先检查慢查询与连接泄漏，再临时调大 max_connections。",
    "错误码 ORD-5003 表示库存服务超时，需要检查 inventory-svc 的熔断状态。",
    "发布回滚：执行 down迁移脚本后，再回滚应用镜像到上一个版本。",
    "磁盘使用率超过 90% 会触发 P2 告警，需要清理日志或扩容。",
]
queries = ["服务连不上数据库，提示没有可用连接了怎么办？", "ORD-5003 是什么错误？"]
D = emb.embed(docs)
def cos(a, b): return sum(x*y for x, y in zip(a, b))
def kw(q, d):  # 极简字面重叠（字符二元组），模拟关键词检索
    g = lambda t: {t[i:i+2] for i in range(len(t)-1)}
    return len(g(q) & g(d)) / max(1, len(g(q)))
for q in queries:
    qv = emb.embed_query(q)
    print("问题:", q)
    for i, d in enumerate(docs):
        print(f"  doc{i}  向量 {cos(qv, D[i]):.3f}  字面 {kw(q, d):.3f}  {d[:22]}…")""")
md("""**观察**：第一个问题（改写）与 doc0 字面重叠很低，但向量相似度最高；第二个问题（精确错误码）字面重叠直接命中 doc1。
真实评测中 q022/q029 属于前者（keyword 未命中、vector 命中），q002/q012 属于后者。

## 2. RRF（Reciprocal Rank Fusion）

RRF 只用名次不用分数：`score(d) = Σ 1/(k + rank_i(d))`，k=60。不同检索器的分数量纲不同（余弦 vs ts_rank），用名次融合最稳健。""")
code("""vector_rank = ["doc0", "doc3", "doc1", "doc2"]
keyword_rank = ["doc1", "doc2", "doc0"]
for doc, sc in rrf([vector_rank, keyword_rank], k=60):
    print(doc, round(sc, 5))""")
md("""两路都靠前的 doc0、doc1 排在最前——这就是 hybrid 在 dev/held-out 上 Recall 都不低于单路的原因。

## 3. Cross-encoder 重排

Bi-encoder 分别编码问题和文档；cross-encoder 把（问题, 文档）拼在一起打分，更准但更慢。
开发中发现 sentence-transformers 在 `activation_fn=None` 时会套用模型默认的 Sigmoid，而我们又做了一次 sigmoid，导致所有分数挤在 0.5–0.73（双 sigmoid）。修复后分数恢复区分度：""")
code("""ce = CrossEncoderReranker(model_id=s.reranker_model, revision=s.reranker_revision, threads=s.local_threads)
q = queries[0]
t0 = time.perf_counter(); scores = ce.score(q, docs); dt = time.perf_counter() - t0
for d, sc in sorted(zip(docs, scores), key=lambda x: -x[1]):
    print(f"{sc:.4f}  double_sigmoid={1/(1+math.exp(-sc)):.4f}  {d[:24]}…")
print(f"4 段耗时 {dt*1000:.0f} ms（CPU）")""")
md("## 4. 真实评测结果：Mock vs 真实 Embedding（读取已提交 JSON）")
code("""print((ROOT / "docs/reports/STAGE2_5_TABLES.md").read_text(encoding="utf-8").split("## 问答拒答：dev")[0])""")
md("""**解读**：bge-small + cross-encoder 是两套数据上唯一 Recall@5=1.0 的配置，但每查询约 2 秒；held-out 上 mock vector 反而高于 bge-small vector——42 个 chunk 的小语料不足以给出"语义一定更好"的强结论。

## 5. held-out 揭示的阈值过拟合""")
code("""txt = (ROOT / "docs/reports/STAGE2_5_TABLES.md").read_text(encoding="utf-8")
print(txt.split("## 问答拒答：heldout")[1].split("## Reranker")[0])""")
md("""dev 上调到 1.000 的阈值，在冻结的 held-out 上正确拒答率只有 0.5–0.6：近话题但无答案的问题（如 h029、h036）连 cross-encoder 都给出高分。
看到 held-out 后**没有**再调参。解决方向是 Stage 3 的可回答性判断（LLM + 引用校验）。

## 6. pgvector iterative scan""")
code("""for f in sorted(glob.glob(str(ROOT / "evals/results/*iterscan*.json"))):
    d = json.load(open(f)); p = d["params"]
    print(f"n={p['n']} 选择度={p['selectivity']} ef_search={p['ef_search']} pgvector={d['pgvector']}")
    for mode, r in d["settings"].items():
        print(f"  {mode:14s} 平均返回 {r['rows_returned_mean']:>5}  召回 {r['recall_vs_exact']:.3f}  P50 {r['latency_ms_p50']} ms")""")
md("""1% 选择度下，关闭 iterative scan 时 HNSW 过滤后几乎返回不了结果（平均 0.37 行），relaxed_order 召回 0.72 最优，因此应用默认 `relaxed_order` 并在外层重新排序。""")

nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
      "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
out = Path(__file__).resolve().parents[1] / "notebooks" / "real_embedding_hybrid.ipynb"
out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print(out)
