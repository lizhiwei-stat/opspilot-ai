import { useEffect, useState } from "react";
import { type EvalCase, type EvalRun, rag } from "../api";
import { Badge, Card, ErrorText, MockNotice, fmt } from "../components/ui";
import { errText } from "../errors";

export default function Evals({ token }: { token: string }) {
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [detail, setDetail] = useState<(EvalRun & { cases: EvalCase[] }) | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    rag.evalRuns(token).then(setRuns).catch((e) => setError(errText(e)));
  }, [token]);

  // newest batch = runs sharing the latest commit+start minute
  const warning = runs.find((r) => r.config.warning)?.config.warning;
  const qa = runs.find((r) => r.metrics.qa)?.metrics.qa;

  // side-by-side matrix: mode x (embedding model, split); latest run per cell
  const colKey = (r: EvalRun) => `${String(r.config.embedding_model ?? "?")} · ${String(r.config.dataset_split ?? "dev")}`;
  const latest = new Map<string, EvalRun>();
  for (const r of runs) {
    const k = `${r.retriever}|${colKey(r)}`;
    const prev = latest.get(k);
    if (!prev || prev.started_at < r.started_at) latest.set(k, r);
  }
  const cols = [...new Set(runs.map(colKey))].sort();
  const modes = [...new Set(runs.map((r) => r.retriever))].sort();

  return (
    <div className="space-y-6">
      {runs.length > 0 && (
        <Card title="Mock vs 真实 Embedding 并排对比（Recall@5 / MRR@10，每格取最新一次）" label="eval-matrix">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-slate-500">
                <tr>
                  <th className="py-1">模式</th>
                  {cols.map((c) => (
                    <th key={c}>
                      {c}
                      {c.startsWith("mock") && <Badge tone="amber">无语义</Badge>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {modes.map((m) => (
                  <tr key={m} className="border-t">
                    <td className="py-1 font-mono">{m}</td>
                    {cols.map((c) => {
                      const r = latest.get(`${m}|${c}`);
                      return <td key={c}>{r ? `${fmt(r.metrics.recall_at_5)} / ${fmt(r.metrics.mrr_at_10)}` : "–"}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      <Card title="检索评测结果" label="evals">
        <ErrorText error={error} />
        {warning && (
          <div className="mb-3">
            <MockNotice>{warning}</MockNotice>
          </div>
        )}
        {runs.length === 0 ? (
          <p className="text-sm text-slate-500">
            还没有评测记录。运行 <code>uv run python evals/run_retrieval_eval.py</code> 后刷新。
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="py-2">模式</th>
                <th>Recall@5</th>
                <th>MRR@10</th>
                <th>nDCG@10</th>
                <th>P50 / P95 ms</th>
                <th>失败率</th>
                <th>ACL 泄露</th>
                <th>Embedding</th>
                <th>commit</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className={`cursor-pointer border-t hover:bg-slate-50 ${detail?.id === r.id ? "bg-indigo-50/50" : ""}`} onClick={() => void rag.evalRun(token, r.id).then(setDetail).catch((e) => setError(errText(e)))}>
                  <td className="py-2 font-mono">{r.retriever}</td>
                  <td>{fmt(r.metrics.recall_at_5)}</td>
                  <td>{fmt(r.metrics.mrr_at_10)}</td>
                  <td>{fmt(r.metrics.ndcg_at_10)}</td>
                  <td>
                    {r.metrics.latency_p50_ms} / {r.metrics.latency_p95_ms}
                  </td>
                  <td>{r.metrics.failure_rate}</td>
                  <td>
                    <Badge tone={r.metrics.acl_leaked_chunks === 0 ? "green" : "red"}>{r.metrics.acl_leaked_chunks}</Badge>
                  </td>
                  <td className="text-xs">
                    {String(r.config.embedding_model ?? "")}
                    {r.config.embedding_has_semantics === false && <Badge tone="amber">无语义</Badge>}
                  </td>
                  <td className="font-mono text-xs">{r.git_commit?.slice(0, 7) ?? "–"}</td>
                  <td className="text-xs">{new Date(r.started_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {qa && (
          <p className="mt-4 text-sm text-slate-600">
            问答（hybrid_rerank + mock 抽取式）：无答案/无权限问题正确拒答率 <b>{fmt(qa.no_answer_correct_refusal_rate)}</b>，可回答问题作答率 <b>{fmt(qa.answerable_answer_rate)}</b>，引用 ACL 泄露 <b>{qa.citation_acl_leaks}</b>。
          </p>
        )}
        <p className="mt-2 text-xs text-slate-500">数据集与语料均为合成数据（label_source=synthetic），规模小，指标只用于对比不同检索模式，不代表生产效果。</p>
      </Card>
      {detail && (
        <Card title={`${detail.name} · 逐条结果（${detail.cases.length}）`} label="eval-cases">
          <table className="w-full text-xs">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-1">case</th>
                <th>类别</th>
                <th>Recall@5</th>
                <th>MRR@10</th>
                <th>延迟 ms</th>
                <th>失败类别</th>
                <th>Top-3</th>
              </tr>
            </thead>
            <tbody>
              {detail.cases.map((c) => (
                <tr key={c.case_id} className="border-t align-top">
                  <td className="py-1 font-mono">{c.case_id}</td>
                  <td>{c.details.category}</td>
                  <td>{fmt(c.recall_at_5, 2)}</td>
                  <td>{fmt(c.details.mrr_at_10, 2)}</td>
                  <td>{c.latency_ms}</td>
                  <td>{c.details.failure_category ? <Badge tone={c.details.failure_category === "ok" ? "green" : "amber"}>{String(c.details.failure_category)}</Badge> : "–"}</td>
                  <td className="text-slate-500">{c.details.top?.slice(0, 3).map((t) => `${t.doc}${t.page ? `#p${t.page}` : ""}`).join("，") || "（无结果）"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
