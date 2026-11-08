import { platform } from "../api";
import { Badge, Card, EmptyState, ErrorText, Loading, PageHeader, RUN_STATUS, Stat, StatusBadge, TICKET_STATUS, btn, pct, when } from "../components/ui";
import { useAsync } from "../lib/hooks";
import { Link } from "../lib/router";
import { useAuth } from "../lib/session";

export default function Dashboard() {
  const { token } = useAuth();
  const { data: d, error, loading, reload } = useAsync(() => platform.dashboard(token), [token]);

  return (
    <div>
      <PageHeader
        title="仪表盘"
        subtitle={d ? `数据来源：PostgreSQL 实时查询 · 范围 ${d.scope === "all" ? "全部" : d.scope === "own" ? "本人" : "全部工单 / 本人运行"} · ${when(d.generated_at)}` : "实时业务数据"}
        actions={
          <button className={btn} onClick={() => void reload()}>
            刷新
          </button>
        }
      />
      <ErrorText error={error} />
      {loading && !d && <Loading />}
      {d && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4" aria-label="ticket-stats">
            <Stat label="工单总数" value={d.tickets.total} sub="来源 tickets" />
            <Stat label="待处理工单" value={d.tickets.pending} sub="open + in_progress" tone={d.tickets.pending ? "red" : "slate"} />
            <Stat label="已完成工单" value={d.tickets.completed} sub="resolved + closed" tone="green" />
            <Stat label="待审批草稿" value={d.tickets.pending_drafts} sub="ticket_drafts.pending_approval" />
          </div>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4" aria-label="agent-stats">
            <Stat label="智能体运行次数" value={d.agent_runs.total} sub="agent_runs（每轮一行）" />
            <Stat
              label="任务成功率"
              value={pct(d.agent_runs.task_success.value)}
              sub={`${d.agent_runs.task_success.numerator} / ${d.agent_runs.task_success.denominator} · completed / 已结束运行`}
            />
            <Stat label="工具调用成功率" value={pct(d.tool_success.value)} sub={`${d.tool_success.numerator} / ${d.tool_success.denominator} · tool_calls.status=ok`} />
            <Stat
              label="运行延迟 P50 / P95"
              value={d.agent_runs.latency.n ? `${d.agent_runs.latency.p50_ms} / ${d.agent_runs.latency.p95_ms} ms` : "不可用"}
              sub={`n=${d.agent_runs.latency.n} · 后端墙钟时间`}
            />
          </div>
          <div className="grid gap-6 lg:grid-cols-3">
            <Card title="近 14 天运行趋势" label="daily-chart">
              {d.agent_runs.daily.length ? <DailyChart rows={d.agent_runs.daily} /> : <EmptyState title="近 14 天无运行记录" />}
            </Card>
            <Card title="Token 与成本" label="usage">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-slate-500">
                  <tr>
                    <th>来源</th>
                    <th>运行</th>
                    <th>Tokens 入/出</th>
                    <th>成本 ¥</th>
                  </tr>
                </thead>
                <tbody>
                  {(["mock", "real"] as const).map((k) => (
                    <tr key={k} className="border-t">
                      <td className="py-1.5">{k === "mock" ? <Badge tone="amber">Mock 估算</Badge> : <Badge tone="indigo">真实 API</Badge>}</td>
                      <td>{d.usage[k].runs}</td>
                      <td>
                        {d.usage[k].tokens_in} / {d.usage[k].tokens_out}
                      </td>
                      <td>{d.usage[k].cost_cny.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-3 text-xs text-slate-500">Mock 的 token 按字符估算，成本恒为 0；真实与 Mock 分开统计，不合并。</p>
            </Card>
            <Card title="状态分布" label="status-breakdown">
              <p className="mb-1 text-xs text-slate-500">工单</p>
              <div className="mb-3 flex flex-wrap gap-2">
                {Object.entries(d.tickets.by_status).map(([k, v]) => (
                  <span key={k} className="text-sm">
                    <StatusBadge map={TICKET_STATUS} value={k} /> {v}
                  </span>
                ))}
                {!Object.keys(d.tickets.by_status).length && <span className="text-xs text-slate-400">无</span>}
              </div>
              <p className="mb-1 text-xs text-slate-500">智能体运行</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(d.agent_runs.by_status).map(([k, v]) => (
                  <span key={k} className="text-sm">
                    <StatusBadge map={RUN_STATUS} value={k} /> {v}
                  </span>
                ))}
                {!Object.keys(d.agent_runs.by_status).length && <span className="text-xs text-slate-400">无</span>}
              </div>
            </Card>
          </div>
          <Card title="最近运行" label="recent-runs">
            {d.recent_runs.length === 0 ? (
              <EmptyState title="还没有智能体运行记录" hint={<Link to="/workspace" className="text-indigo-600 underline">去智能体工作台发起第一个问题</Link>} />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-slate-500">
                    <tr>
                      <th className="py-1">时间</th>
                      <th>问题</th>
                      <th>意图</th>
                      <th>状态</th>
                      <th>延迟</th>
                      <th>模型</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.recent_runs.map((r) => (
                      <tr key={r.run_id} className="border-t">
                        <td className="whitespace-nowrap py-1.5 text-xs text-slate-500">{when(r.created_at)}</td>
                        <td>
                          <Link to={`/executions/${r.run_id}`} className="text-indigo-700 hover:underline">
                            {r.question}
                          </Link>
                        </td>
                        <td className="text-xs">{r.intent ?? "–"}</td>
                        <td>
                          <StatusBadge map={RUN_STATUS} value={r.status} />
                        </td>
                        <td className="text-xs">{r.latency_ms ?? "–"} ms</td>
                        <td className="text-xs">
                          {r.llm} {r.is_mock && <Badge tone="amber">Mock</Badge>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

function DailyChart({ rows }: { rows: { day: string; runs: number; completed: number }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.runs));
  const w = 28;
  return (
    <svg role="img" aria-label="每日运行次数柱状图" viewBox={`0 0 ${rows.length * w + 10} 130`} className="h-40 w-full">
      {rows.map((r, i) => {
        const h = (r.runs / max) * 100;
        const hc = (r.completed / max) * 100;
        return (
          <g key={r.day} transform={`translate(${i * w + 5},0)`}>
            <title>{`${r.day}: ${r.runs} 次运行，${r.completed} 次完成`}</title>
            <rect x={2} y={105 - h} width={w - 8} height={h} className="fill-indigo-200" />
            <rect x={2} y={105 - hc} width={w - 8} height={hc} className="fill-indigo-600" />
            <text x={w / 2 - 2} y={122} textAnchor="middle" className="fill-slate-500 text-[8px]">
              {r.day.slice(5)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
