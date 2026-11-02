import { type FormEvent, useState } from "react";
import { MODES, type Mode, type SearchResult, rag } from "../api";
import { Badge, Card, ErrorText, MockNotice, btnPrimary, fmt, input } from "../components/ui";
import { errText } from "../errors";

export const MODE_LABEL: Record<Mode, string> = {
  vector: "向量",
  keyword: "关键词",
  hybrid: "混合 RRF",
  hybrid_rerank: "混合 + 重排",
};

export default function SearchDebug({ token, embeddingProvider }: { token: string; embeddingProvider?: string }) {
  const [query, setQuery] = useState("ERR_PAY_TIMEOUT 之后怎么重试");
  const [topK, setTopK] = useState(5);
  const [results, setResults] = useState<Partial<Record<Mode, SearchResult>>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSearch(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const all = await Promise.all(MODES.map((m) => rag.search(token, query, m, topK)));
      setResults(Object.fromEntries(all.map((r) => [r.mode, r])));
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }
  const any = results.hybrid ?? results.keyword;

  return (
    <div className="space-y-6">
      <Card title="检索调试 · 四种模式并排对比" label="search-debug">
        <form onSubmit={onSearch} className="flex flex-wrap items-end gap-3">
          <label className="flex-1 text-sm">
            查询
            <input aria-label="查询" className={`${input} mt-1 w-full`} value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <label className="text-sm">
            Top-K
            <input aria-label="Top-K" type="number" min={1} max={20} className={`${input} mt-1 w-20`} value={topK} onChange={(e) => setTopK(Number(e.target.value))} />
          </label>
          <button className={btnPrimary} disabled={busy || !query.trim()}>
            {busy ? "检索中…" : "检索"}
          </button>
        </form>
        <div className="mt-3 space-y-2">
          <ErrorText error={error} />
          {embeddingProvider === "mock" && (
            <MockNotice>当前 Embedding 为 mock（特征哈希，仅反映字面重叠，没有语义理解）。向量 / 混合结果不代表真实向量模型效果。</MockNotice>
          )}
          {any && (
            <p className="text-xs text-slate-500">
              分词：{any.tokens.map((t) => <code key={t} className="mr-1 rounded bg-slate-100 px-1">{t}</code>)} · embedding 模型 {any.embedding_model}
            </p>
          )}
        </div>
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        {MODES.filter((m) => results[m]).map((m) => {
          const r = results[m] as SearchResult;
          return (
            <Card
              key={m}
              label={`results-${m}`}
              title={
                <span>
                  {MODE_LABEL[m]} <span className="font-mono text-xs text-slate-400">{m}</span>
                </span>
              }
              actions={<span className="text-xs text-slate-500">{Object.entries(r.timings_ms).map(([k, v]) => `${k}=${v.toFixed(1)}`).join(" · ")}</span>}
            >
              {r.candidates.length === 0 ? (
                <p className="text-sm text-slate-500">无结果（可能没有匹配，或文档不在你的权限范围内）</p>
              ) : (
                <ol className="space-y-2">
                  {r.candidates.map((c) => (
                    <li key={c.chunk_id} className="rounded-lg border border-slate-200 p-3 text-sm">
                      <div className="mb-1 flex flex-wrap items-center gap-2">
                        <Badge tone="indigo">#{c.rank}</Badge>
                        <span className="font-medium">{c.document_name}</span>
                        <span className="text-xs text-slate-500">
                          v{c.version}
                          {c.page_number ? ` · 第 ${c.page_number} 页` : ""}
                          {c.heading_path ? ` · ${c.heading_path}` : ""} · {c.kb_slug}
                        </span>
                        {c.suspicious && <Badge tone="red" title="包含疑似提示注入的内容，问答时会被剔除">可疑内容</Badge>}
                      </div>
                      <p className="line-clamp-3 text-slate-700">{c.content}</p>
                      <table className="mt-2 text-xs text-slate-500">
                        <tbody>
                          <tr>
                            {(["vector", "keyword", "rrf", "rerank"] as const).map((k) => (
                              <td key={k} className="pr-4">
                                {k}: {fmt(c.scores[k], 4)}
                                {c.ranks[k] ? ` (#${c.ranks[k]})` : ""}
                              </td>
                            ))}
                          </tr>
                        </tbody>
                      </table>
                    </li>
                  ))}
                </ol>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
