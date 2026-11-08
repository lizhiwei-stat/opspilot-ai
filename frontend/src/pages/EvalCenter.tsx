import { useMemo, useState } from "react";
import { type EvalRow, platform } from "../api";
import { Badge, Card, EmptyState, ErrorText, Loading, MockNotice, PageHeader, Tabs, input, select } from "../components/ui";
import { useAsync } from "../lib/hooks";
import { navigate } from "../lib/router";
import { useAuth } from "../lib/session";
import Evals from "./Evals";

const METRICS: [string, string, (v: number) => string][] = [
  ["recall_at_5", "Recall@5", (v) => v.toFixed(3)],
  ["mrr_at_10", "MRR@10", (v) => v.toFixed(3)],
  ["task_success_rate", "任务成功", (v) => `${(v * 100).toFixed(1)}%`],
  ["refusal_rate", "正确拒答", (v) => `${(v * 100).toFixed(1)}%`],
  ["correct_refusal_rate", "正确拒答(Agent)", (v) => `${(v * 100).toFixed(1)}%`],
  ["failure_rate", "失败率", (v) => `${(v * 100).toFixed(1)}%`],
  ["latency_p50_ms", "P50 ms", (v) => v.toFixed(0)],
  ["latency_p95_ms", "P95 ms", (v) => v.toFixed(0)],
  ["tokens", "Tokens", (v) => String(v)],
  ["cost_cny", "成本 ¥", (v) => v.toFixed(4)],
];

export default function EvalCenter({ tab }: { tab: string }) {
  const { token } = useAuth();
  const t = tab === "rag-db" ? "rag-db" : "results";
  return (
    <div>
      <PageHeader title="评测与监控" subtitle="只展示仓库中真实生成的评测记录；Mock 与真实分开标注；不同数据集/划分/提供方不合并计算" />
      <Tabs
        label="评测视图"
        value={t}
        onChange={(v) => navigate(v === "results" ? "/evals" : "/evals?tab=rag-db")}
        items={[
          ["results", "评测结果（文件）"],
          ["rag-db", "RAG 评测运行（数据库）"],
        ]}
      />
      {t === "results" ? <Results /> : <Evals token={token} />}
    </div>
  );
}

function Results() {
  const { token } = useAuth();
  const res = useAsync(() => platform.evalResults(token), [token]);
  const [f, setF] = useState({ kind: "", source: "", model: "", dataset: "", version: "", from: "", to: "" });
  const rows = useMemo(() => res.data?.rows ?? [], [res.data]);
  const uniq = (k: keyof EvalRow) => [...new Set(rows.map((r) => String(r[k] ?? "")).filter(Boolean))].sort();
  const filtered = useMemo(
    () =>
      rows.filter((r) => {
        const day = (r.generated_at ?? "").replace(/^(\d{4})(\d{2})(\d{2})T.*/, "$1-$2-$3").slice(0, 10);
        return (
          (!f.kind || r.kind === f.kind) &&
          (!f.source || (f.source === "mock") === r.is_mock) &&
          (!f.model || r.model === f.model) &&
          (!f.dataset || r.dataset === f.dataset) &&
          (!f.version || r.version === f.version) &&
          (!f.from || day >= f.from) &&
          (!f.to || day <= f.to)
        );
      }),
    [rows, f],
  );
  // one table per comparable group: same kind + dataset + split + provider + model
  const groups = useMemo(() => {
    const m = new Map<string, EvalRow[]>();
    for (const r of filtered) {
      const k = [r.kind, r.dataset, r.split ?? "-", r.provider, r.model].join(" | ");
      m.set(k, [...(m.get(k) ?? []), r]);
    }
    return [...m.entries()];
  }, [filtered]);

  if (res.error) return <ErrorText error={res.error} />;
  if (!res.data) return <Loading />;
  if (!res.data.available) return <EmptyState title="评测结果目录不可用" hint={res.data.source} />;
  return (
    <div className="space-y-4">
      <Card title="筛选" label="eval-filters">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
          <select aria-label="类型" className={select} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
            <option value="">全部类型</option>
            <option value="rag">RAG 检索/问答</option>
            <option value="agent">Agent</option>
          </select>
          <select aria-label="来源" className={select} value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })}>
            <option value="">Mock + 真实</option>
            <option value="mock">仅 Mock</option>
            <option value="real">仅真实</option>
          </select>
          <select aria-label="模型" className={select} value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })}>
            <option value="">全部模型</option>
            {uniq("model").map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
          <select aria-label="数据集" className={select} value={f.dataset} onChange={(e) => setF({ ...f, dataset: e.target.value })}>
            <option value="">全部数据集</option>
            {uniq("dataset").map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
          <select aria-label="版本" className={select} value={f.version} onChange={(e) => setF({ ...f, version: e.target.value })}>
            <option value="">全部版本</option>
            {uniq("version").map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
          <input aria-label="开始日期" type="date" className={input} value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} />
          <input aria-label="结束日期" type="date" className={input} value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} />
        </div>
        <p className="mt-2 text-xs text-slate-500">
          {filtered.length} / {rows.length} 行 · 来源 {res.data.source} · 跳过的非评测文件 {res.data.skipped_files.length} 个 · “不可用”= 该评测未测量此指标
        </p>
      </Card>
      {groups.length === 0 && <EmptyState title="没有符合条件的评测记录" />}
      {groups.map(([k, rs]) => (
        <Card key={k} title={<span className="text-sm">{k}</span>} label="eval-group">
          {rs.some((r) => r.is_mock) && <MockNotice>{rs.find((r) => r.disclaimer)?.disclaimer ?? "Mock 结果：仅验证流程与工程链路，不代表真实模型效果。"}</MockNotice>}
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-slate-500">
                <tr>
                  <th className="py-1">时间</th>
                  <th>模式</th>
                  <th>来源</th>
                  <th>环境</th>
                  <th>版本</th>
                  <th>n</th>
                  {METRICS.map(([key, label]) => (
                    <th key={key}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rs.map((r, i) => (
                  <tr key={`${r.file}-${r.mode}-${i}`} className="border-t" title={r.file}>
                    <td className="whitespace-nowrap py-1">{r.generated_at?.slice(0, 16)}</td>
                    <td className="font-mono">{r.mode}</td>
                    <td>{r.is_mock ? <Badge tone="amber">Mock</Badge> : <Badge tone="indigo">真实</Badge>}</td>
                    <td>{r.environment}</td>
                    <td className="font-mono">{r.version || "–"}</td>
                    <td>{r.n ?? "–"}</td>
                    {METRICS.map(([key, , fmt]) => {
                      const v = r.metrics[key];
                      return (
                        <td key={key} className={v === null || v === undefined ? "text-slate-300" : ""}>
                          {v === null || v === undefined ? "不可用" : fmt(v)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ))}
    </div>
  );
}
