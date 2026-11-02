"""Generate notebooks/rag_walkthrough.ipynb (source of truth for the notebook cells).

Execute with outputs kept:
  uv run --group notebook jupyter nbconvert --to notebook --execute --inplace notebooks/rag_walkthrough.ipynb
"""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf

M, C = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
cells = [
    M("""# OpsPilot AI · 第二阶段 RAG 流程演示（可复现）

本 Notebook 逐步运行真实代码（与后端服务同一套模块），连接本地 PostgreSQL + pgvector：

解析 → 分块 → 分词 → 向量化 → 入库 → 各检索器 → 手算 RRF → 重排 → 带引用的回答 → 评测指标。

> ⚠️ **诚实声明**：默认使用 **mock embedding**（特征哈希的字/词袋向量）。它只反映**字面重叠**，**没有任何语义理解**；
> mock 回答器是**抽取式**的，没有调用大模型。语料和评测集均为**合成数据**。下面所有数字都只能说明流程正确，不能说明真实模型效果。"""),
    C("""import os, sys, json, math
from pathlib import Path
ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path[:0] = [str(ROOT / "backend" / "src"), str(ROOT / "scripts"), str(ROOT)]
os.chdir(ROOT)
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")  # DB 连接等；不读取也不打印任何密钥

from opspilot.core.config import get_settings
settings = get_settings()
assert settings.embedding_provider == "mock" and settings.llm_provider == "mock", "本 Notebook 只在 mock 模式下运行"
print("embedding provider:", settings.embedding_provider, "| llm provider:", settings.llm_provider)
print("chunk_size =", settings.chunk_size, "overlap =", settings.chunk_overlap, "rrf_k =", settings.rrf_k)"""),
    M(
        "## 1. 解析：PDF 按页解析 + 清洗\n\n校验扩展名白名单、大小、魔数（`%PDF-`）与扩展名一致性；PDF 逐页抽取文本，修复逐字加空格、合并视觉换行。"
    ),
    C("""from opspilot.rag.parsers import validate_and_parse, ParseError
pdf_bytes = (ROOT / "evals/corpus/order-platform/deployment-rollback-guide.pdf").read_bytes()
doc = validate_and_parse("deployment-rollback-guide.pdf", pdf_bytes, max_bytes=10 * 2**20)
print(doc.kind, doc.mime_type, "pages:", len(doc.pages))
for p in doc.pages:
    print(f"--- 第 {p.page_number} 页 ({len(p.text)} 字) ---")
    print(p.text[:160])
for name, data in [("x.pdf", b"not a pdf"), ("x.exe", b"MZ"), ("x.txt", b"bin\\x00")]:
    try:
        validate_and_parse(name, data, max_bytes=1024)
    except ParseError as e:
        print("拒绝", name, "->", e.code, e)"""),
    M(
        "## 2. 分块：标题感知 + 中英文分隔符\n\n先按 Markdown 标题切节（记录 `heading_path`），再用 LangChain `RecursiveCharacterTextSplitter`，分隔符优先级：段落 → 换行 → 中文句号/问号/分号 → 英文句末 → 逗号 → 空格。偏移量精确可回溯。"
    ),
    C("""from opspilot.rag.chunking import chunk_document, SEPARATORS
md = (ROOT / "evals/corpus/order-platform/order-service-http500-runbook.md").read_bytes()
mdoc = validate_and_parse("runbook.md", md, max_bytes=10 * 2**20)
chunks = chunk_document(mdoc, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
print("separators:", SEPARATORS[:8], "...")
text = mdoc.pages[0].text
for c in chunks:
    assert text[c.char_start:c.char_end] == c.content  # 偏移量不变式
    print(f"#{c.index} [{c.char_start}:{c.char_end}] {len(c.content):>3} 字 | {c.heading_path}")
pdf_chunks = chunk_document(doc, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
print("PDF 分块页码:", [c.page_number for c in pdf_chunks])"""),
    M(
        "## 3. 分词：jieba + 代码标识符归一化\n\nPostgreSQL 内置解析器不会切中文，也会把 `ERR_PAY_TIMEOUT` 按 `_` 拆散。我们在写入前用 jieba（搜索模式 + 领域词典）分词，错误码整体保留并归一化（`errpaytimeout`），同时保留其组成部分。"
    ),
    C("""from opspilot.rag.tokenize import tokenize, query_tokens, to_search_text
for s in ["日志出现 ERR_PAY_TIMEOUT 和 ORD-5003", "数据库连接池耗尽怎么办？", "PaymentAdapter.charge() 空指针"]:
    print(s, "->", tokenize(s))"""),
    M(
        "## 4. 向量化（mock）\n\n下面用三句话直观展示 mock embedding 的局限：同义改写但没有共同字词时，相似度很低。"
    ),
    C("""from opspilot.llm.factory import build_embedding
emb = build_embedding(settings)
a, b, c = emb.embed(["发版后出问题怎么退回旧版本", "发布导致故障时优先回滚", "发版后出问题怎么退回"])
cos = lambda x, y: sum(i * j for i, j in zip(x, y))
print("dim =", emb.dim, "model =", emb.model)
print("改写(语义相同,字面不同) cos =", round(cos(a, b), 3))
print("字面几乎相同            cos =", round(cos(a, c), 3))"""),
    M(
        "## 5. 入库：通过真实 IngestionService 导入合成语料\n\n内容哈希去重、版本化、分块与向量同事务写入。重复运行会显示 `duplicate`。"
    ),
    C("""from sqlalchemy import select, text
from sqlalchemy.orm import Session
from opspilot.db.session import build_engine
from load_demo_corpus import load
engine = build_engine(settings)
with Session(engine) as s, s.begin():
    rows = load(s)
for r in rows:
    print(f"{r[0]:16} {r[1]:40} {r[2]:9} chunks={r[3]}")
with Session(engine) as s:
    print(s.execute(text(\"\"\"SELECT kb.slug, kb.visibility, count(c.id) FROM knowledge_bases kb
        LEFT JOIN chunks c ON c.kb_id = kb.id AND c.is_active GROUP BY 1,2 ORDER BY 1\"\"\")).all())"""),
    M("## 6. 各检索器（以 alice 的身份，ACL 在 SQL 里过滤）"),
    C("""from opspilot.db.models import User
from opspilot.services.retrieval import RetrievalService, MODES
s = Session(engine)
users = {u.email.split("@")[0]: u for u in s.scalars(select(User))}
retr = RetrievalService(s, settings, emb)
q = "ERR_PAY_TIMEOUT 之后客户端怎么重试"
res = {m: retr.search(users["alice"], q, mode=m, top_k=5) for m in MODES}
print("query tokens:", res["keyword"].tokens)
for m, r in res.items():
    print(f"\\n== {m}  ({r.timings_ms['total_ms']:.1f} ms)")
    for c in r.candidates:
        print(f"  #{c.rank} {c.score:.4f}  {c.locator[:70]}")"""),
    M(
        "## 7. 手算 RRF，并与服务输出对比\n\n$$\\mathrm{RRF}(d)=\\sum_{m\\in\\{vec,kw\\}} \\frac{1}{k+\\mathrm{rank}_m(d)},\\quad k=60$$"
    ),
    C("""from opspilot.repositories.knowledge import KnowledgeRepository
repo = KnowledgeRepository(s)
vec = repo.vector_search(users["alice"], emb.embed([q])[0], model=emb.model, limit=settings.retrieval_candidates)
kw = repo.keyword_search(users["alice"], query_tokens(q), limit=settings.retrieval_candidates)
manual = {}
for lst in (vec, kw):
    for rank, h in enumerate(lst, start=1):
        manual[h.chunk_id] = manual.get(h.chunk_id, 0) + 1 / (settings.rrf_k + rank)
top = sorted(manual.items(), key=lambda kv: -kv[1])[:5]
svc = [(c.chunk_id, c.score) for c in res["hybrid"].candidates]
for (cid, sc), (cid2, sc2) in zip(top, svc):
    print(f"manual {sc:.6f}  service {sc2:.6f}  same_chunk={str(cid) == cid2}")"""),
    M(
        "## 8. 重排（lexical-overlap-v1）\n\n重排器对（查询, 段落）成对打分：IDF 加权的查询词覆盖率 + 错误码精确命中加分。它仍然是**字面**信号，无法理解同义改写；接口（`Reranker` Protocol）可替换为交叉编码器。"
    ),
    C("""for c in res["hybrid_rerank"].candidates:
    print(f"#{c.rank} rerank={c.scores['rerank']:.3f} rrf_rank={c.ranks['rrf']}  {c.document_name}")"""),
    M(
        "## 9. ACL：同一个问题，不同用户\n\n`hr-confidential` 只有管理员可读；bob 没有 `order-platform` 的授权。"
    ),
    C("""for who in ["admin", "alice", "bob"]:
    r = retr.search(users[who], "值班补贴一周多少钱", mode="hybrid", top_k=3)
    print(who, [(c.kb_slug, c.document_name) for c in r.candidates])"""),
    M("## 10. 带引用的回答（mock 抽取式）、证据不足、提示注入"),
    C("""from opspilot.llm.factory import build_llm
from opspilot.services.qa import QAService
qa = QAService(s, settings, emb, build_llm(settings))
for who, question in [("alice", "ORD-5003 是什么错误，怎么处理？"), ("alice", "公司年会在哪里举办？"),
                      ("bob", "值班补贴一周多少钱？"), ("bob", "物流运单号是什么格式？")]:
    out = qa.answer(users[who], question)
    print(f"\\n[{who}] {question}\\n generator={out.generator} evidence={out.evidence_score} sufficient={out.evidence_sufficient}")
    print(" ", out.answer.replace("\\n", "\\n  "))
    for cit in out.citations:
        print(f"   [{cit.index}] {cit.document_name} p={cit.page_number} chunk={cit.chunk_id[:8]}")
    if out.flagged_chunks:
        print("  已剔除疑似注入内容:", out.flagged_chunks)"""),
    M(
        "## 11. 评测指标\n\n读取 `evals/run_retrieval_eval.py` 最近一次的结果（如不存在则现场跑一次）。"
    ),
    C("""import subprocess
latest = ROOT / "evals/results/latest_mock.json"
if not latest.exists():
    subprocess.run([sys.executable, "evals/run_retrieval_eval.py", "--no-db"], check=True, capture_output=True)
rep = json.loads(latest.read_text(encoding="utf-8"))
print(rep["meta"]["warning"])
print(f"{'mode':15}{'R@5':>7}{'MRR@10':>8}{'nDCG@10':>9}{'P50ms':>8}{'P95ms':>8}{'leak':>6}")
for m, x in rep["retrieval"].items():
    print(f"{m:15}{x['recall_at_5']:>7.3f}{x['mrr_at_10']:>8.3f}{x['ndcg_at_10']:>9.3f}{x['latency_p50_ms']:>8}{x['latency_p95_ms']:>8}{x['acl_leaked_chunks']:>6}")
print("QA:", {k: v for k, v in rep["qa"].items() if k != "by_category"})
s.close()"""),
]
nb = nbf.v4.new_notebook(
    cells=cells,
    metadata={"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}},
)
out = Path(__file__).resolve().parents[1] / "notebooks" / "rag_walkthrough.ipynb"
nbf.write(nb, out)
print("wrote", out)
