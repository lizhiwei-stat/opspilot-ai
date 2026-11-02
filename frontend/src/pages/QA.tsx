import { type FormEvent, useState } from "react";
import { type QAResult, rag } from "../api";
import { Badge, Card, ErrorText, MockNotice, btnPrimary, fmt, input } from "../components/ui";
import { errText } from "../errors";

export default function QA({ token }: { token: string }) {
  const [q, setQ] = useState("ORD-5003 是什么错误，怎么处理？");
  const [res, setRes] = useState<QAResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onAsk(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setRes(await rag.qa(token, q));
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card title="知识库问答（带引用）" label="qa">
        <form onSubmit={onAsk} className="flex gap-3">
          <input aria-label="问题" className={`${input} flex-1`} value={q} onChange={(e) => setQ(e.target.value)} />
          <button className={btnPrimary} disabled={busy || !q.trim()}>
            {busy ? "回答中…" : "提问"}
          </button>
        </form>
        <div className="mt-3">
          <ErrorText error={error} />
        </div>
      </Card>
      {res && (
        <Card
          label="answer"
          title="回答"
          actions={
            <div className="flex gap-2">
              <Badge tone={res.is_mock ? "amber" : "indigo"}>生成器：{res.generator}</Badge>
              <Badge tone={res.evidence_sufficient ? "green" : "red"}>
                证据 {fmt(res.evidence_score, 2)} / 阈值 {fmt(res.threshold, 2)}
              </Badge>
            </div>
          }
        >
          {res.is_mock && (
            <div className="mb-3">
              <MockNotice>这是 Mock 抽取式回答：没有调用大模型，只是按问题关键词从检索到的原文中摘句，并附上真实引用。</MockNotice>
            </div>
          )}
          <pre className={`whitespace-pre-wrap rounded-lg p-4 font-sans text-sm ${res.evidence_sufficient ? "bg-slate-50" : "bg-rose-50 text-rose-800"}`}>{res.answer}</pre>
          {res.flagged_chunks.length > 0 && (
            <p className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-700">
              ⚠️ 检索结果中有 {res.flagged_chunks.length} 个分块包含疑似提示注入内容，相关行已在生成前剔除：
              {res.flagged_chunks.map((f) => `${f.document_name}（${f.removed_lines} 行）`).join("、")}
            </p>
          )}
          {res.citations.length > 0 && (
            <>
              <h3 className="mb-2 mt-5 text-sm font-medium">引用</h3>
              <ol className="space-y-2" aria-label="citations">
                {res.citations.map((c) => (
                  <li key={c.chunk_id} className="rounded-lg border border-slate-200 p-3 text-sm">
                    <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                      <Badge tone="indigo">[{c.index}]</Badge>
                      <span className="font-medium text-slate-700">{c.document_name}</span>
                      {c.page_number && <Badge tone="sky">第 {c.page_number} 页</Badge>}
                      <span>{c.locator}</span>
                      <span>证据 {fmt(c.evidence, 2)}</span>
                    </div>
                    <blockquote className="border-l-2 border-indigo-200 pl-3 text-slate-700">{c.quote}</blockquote>
                    <div className="mt-1 font-mono text-[11px] text-slate-400">chunk {c.chunk_id}</div>
                  </li>
                ))}
              </ol>
            </>
          )}
          <details className="mt-4 text-xs text-slate-500">
            <summary className="cursor-pointer">检索候选（{res.retrieval_mode}）与耗时</summary>
            <ul className="mt-2 space-y-1">
              {res.candidates.map((c) => (
                <li key={c.chunk_id}>
                  #{c.rank} {c.locator} · score {fmt(c.score, 3)} · 证据 {fmt(c.evidence, 2)} {c.suspicious ? "· 可疑" : ""}
                </li>
              ))}
            </ul>
            <p className="mt-2">{Object.entries(res.timings_ms).map(([k, v]) => `${k}=${v.toFixed(1)}ms`).join(" · ")}</p>
          </details>
        </Card>
      )}
    </div>
  );
}
