import { platform, type ObservabilityView } from "../api";
import { Badge, Card, EmptyState, ErrorText, Loading, PageHeader, Stat, when } from "../components/ui";
import { useAsync } from "../lib/hooks";
import { useAuth } from "../lib/session";

export default function Observability() {
  const { token } = useAuth();
  const data = useAsync(() => platform.observability(token), [token]);
  const d = data.data;
  return (
    <div className="space-y-6">
      <PageHeader
        title="可观测性"
        subtitle="轻量方案：结构化 JSON 日志 + 进程内 span 环形缓冲 + Prometheus 文本指标（/metrics）。未引入 Redis / Grafana / 完整 OTel，避免单机部署的额外运维成本；持久轨迹仍在 agent_events、tool_calls、mcp_tool_audit 与模型账本中。"
      />
      <ErrorText error={data.error} />
      {data.loading && !d && <Loading label="加载观测数据…" />}
      {d && <Body d={d} onReload={() => void data.reload()} />}
    </div>
  );
}

function Body({ d, onReload }: { d: ObservabilityView; onReload: () => void }) {
  const c = d.counters;
  const httpOkRate =
    c.http_requests_total > 0
      ? (((c.http_requests_total - c.http_errors_total) / c.http_requests_total) * 100).toFixed(1) + "%"
      : "—";
  return (
    <>
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">{d.note}</p>
        <button type="button" className="rounded-md border px-3 py-1 text-xs" onClick={onReload}>
          刷新
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="HTTP 请求" value={c.http_requests_total} />
        <Stat label="Agent 运行" value={c.agent_runs_total} sub={`近 1h 持久化 ${d.durable.agent_runs_last_hour}`} />
        <Stat
          label="MCP 调用"
          value={c.mcp_calls_total}
          sub={`近 1h 错误 ${d.durable.mcp_errors_last_hour}/${d.durable.mcp_calls_last_hour}`}
        />
        <Stat label="模型调用" value={c.model_calls_total} sub={`估算费用 ¥${Number(c.estimated_cost_cny_total || 0).toFixed(4)}`} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="延迟分布（进程内）" label="latency">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-slate-500">
              <tr>
                <th>类型</th>
                <th>n</th>
                <th>p50</th>
                <th>p95</th>
                <th>p99</th>
                <th>max</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(d.latency_ms).map(([k, row]) => (
                <tr key={k} className="border-t">
                  <td className="py-1 font-mono text-xs">{k}</td>
                  <td>{row.count}</td>
                  <td>{row.p50 ?? "—"}</td>
                  <td>{row.p95 ?? "—"}</td>
                  <td>{row.p99 ?? "—"}</td>
                  <td>{row.max ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-slate-500">
            Token：prompt {c.tokens_prompt_total} / completion {c.tokens_completion_total} · 检索 {c.retrieval_calls_total} · HTTP 错误{" "}
            {c.http_errors_total}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            也可抓取 <code className="font-mono">GET /metrics</code>（Prometheus 文本，无密钥）。
          </p>
        </Card>
        <Card title="近 1 小时 Agent 运行（数据库）" label="recent-runs">
          {!d.durable.recent_runs.length && <EmptyState title="暂无运行" />}
          <ul className="max-h-80 space-y-1 overflow-auto text-sm">
            {d.durable.recent_runs.map((r) => (
              <li key={r.run_id} className="flex flex-wrap items-center gap-2 border-b border-slate-100 py-1">
                <Badge tone={r.status === "completed" ? "green" : r.status === "failed" ? "red" : "amber"}>{r.status}</Badge>
                <span className="font-mono text-[11px] text-slate-500">{r.run_id.slice(0, 8)}</span>
                <span className="text-xs text-slate-500">{r.latency_ms ?? "–"} ms</span>
                {r.is_mock && <Badge tone="amber">Mock</Badge>}
                <span className="text-xs text-slate-400">{r.created_at ? when(r.created_at) : ""}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card title="最近 Span（脱敏后）" label="spans">
        {!d.recent_spans.length && <EmptyState title="尚无 span（发几个请求后再刷新）" />}
        <ol className="max-h-96 space-y-1 overflow-auto font-mono text-[11px]">
          {d.recent_spans.map((s, i) => (
            <li key={`${s.trace_id}-${s.name}-${i}`} className="flex flex-wrap gap-2 border-b border-slate-50 py-1">
              <span className="text-slate-400">{s.kind}</span>
              <span className="font-semibold">{s.name}</span>
              <span
                className={
                  String(s.status).startsWith("2") || s.status === "ok" || s.status === "completed"
                    ? "text-emerald-700"
                    : "text-rose-700"
                }
              >
                {s.status}
              </span>
              <span>{s.duration_ms} ms</span>
              {s.error_type && <span className="text-rose-600">{s.error_type}</span>}
              <span className="truncate text-slate-400">trace={s.trace_id.slice(0, 10)}…</span>
              {s.thread_id && <span className="text-slate-400">thread={String(s.thread_id).slice(0, 8)}</span>}
            </li>
          ))}
        </ol>
        <p className="mt-2 text-xs text-slate-500">成功率示意（HTTP）：{httpOkRate}</p>
      </Card>
    </>
  );
}
