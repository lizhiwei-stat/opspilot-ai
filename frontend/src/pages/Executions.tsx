import { useEffect, useState } from "react";
import { agent } from "../api";
import { ExecutionGraph, RunDetails } from "../components/RunViz";
import { Card, EmptyState, ErrorText, Loading, PageHeader, RUN_STATUS, StatusBadge, btn, select, when } from "../components/ui";
import { useAsync } from "../lib/hooks";
import { Link } from "../lib/router";
import { useAuth } from "../lib/session";
import { type AgentEvent, streamRunEvents } from "../lib/sse";

export default function Executions({ runId }: { runId?: string }) {
  const { token } = useAuth();
  const runs = useAsync(() => agent.list(token), [token]);
  const [status, setStatus] = useState("");
  const rows = (runs.data ?? []).filter((r) => !status || r.status === status);

  return (
    <div>
      <PageHeader
        title="执行可视化"
        subtitle="节点顺序、当前节点、工具、耗时、状态与审批等待；数据来自 agent_runs / agent_events / LangGraph 检查点"
        actions={
          <button className={btn} onClick={() => void runs.reload()}>
            刷新
          </button>
        }
      />
      <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
        <Card
          title="运行列表"
          label="run-list"
          actions={
            <select aria-label="状态筛选" className={select} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">全部状态</option>
              {Object.entries(RUN_STATUS).map(([k, [l]]) => (
                <option key={k} value={k}>
                  {l}
                </option>
              ))}
            </select>
          }
        >
          <ErrorText error={runs.error} />
          {runs.loading && !runs.data && <Loading />}
          {runs.data && rows.length === 0 && <EmptyState title="没有符合条件的运行" />}
          <ul className="max-h-[70vh] space-y-1 overflow-auto">
            {rows.map((r) => (
              <li key={r.run_id}>
                <Link
                  to={`/executions/${r.run_id}`}
                  aria-current={r.run_id === runId ? "page" : undefined}
                  className={`block rounded-md border px-3 py-2 text-sm ${r.run_id === runId ? "border-indigo-400 bg-indigo-50" : "border-slate-200 hover:bg-slate-50"}`}
                >
                  <span className="line-clamp-1">{r.question}</span>
                  <span className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
                    <StatusBadge map={RUN_STATUS} value={r.status} /> 第 {r.turn} 轮 · {when(r.created_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
        {runId ? <RunView runId={runId} /> : <EmptyState title="选择左侧一个运行查看执行过程" />}
      </div>
    </div>
  );
}

function RunView({ runId }: { runId: string }) {
  const { token } = useAuth();
  const run = useAsync(() => agent.get(token, runId), [token, runId]);
  const hist = useAsync(() => agent.history(token, runId), [token, runId]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [ended, setEnded] = useState(false);

  useEffect(() => {
    setEvents([]);
    setEnded(false);
    return streamRunEvents({
      token,
      runId,
      onEvent: (e) => setEvents((x) => [...x, e]),
      onState: (s) => {
        if (s.kind === "ended") {
          setEnded(true);
          void run.reload();
        }
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, runId]);

  if (run.error) return <ErrorText error={run.error} />;
  if (!run.data) return <Loading />;
  const r = run.data;
  const lastNode = [...events].reverse().find((e) => e.type === "node")?.data.node as string | undefined;
  return (
    <div className="min-w-0 space-y-6">
      <Card title={r.question} label="run-header">
        <p className="text-xs text-slate-500">
          run {r.run_id} · thread{" "}
          <Link to={`/workspace/${r.thread_id}`} className="text-indigo-600 underline">
            {r.thread_id}
          </Link>{" "}
          · 第 {r.turn} 轮 · {when(r.created_at)}
        </p>
        {r.status === "awaiting_approval" && <p className="mt-2 rounded bg-amber-50 px-3 py-2 text-sm text-amber-900">⏸ 运行暂停在「人工审批」节点，等待系统管理员决策（检查点已持久化）。</p>}
      </Card>
      <Card title="执行图" label="exec-graph">
        <ExecutionGraph trace={r.node_trace} status={r.status} current={r.status === "running" ? (lastNode ?? null) : null} tools={r.tools} />
      </Card>
      <div className="grid gap-6 xl:grid-cols-2">
        <Card title={`事件（${events.length}）${ended ? "" : " · 跟随中"}`} label="exec-events">
          {events.length === 0 ? (
            <EmptyState title="无事件记录" hint="Stage 5 之前的历史运行没有事件表记录" />
          ) : (
            <ol className="max-h-80 space-y-1 overflow-auto font-mono text-[11px]">
              {events.map((e) => (
                <li key={e.seq}>
                  <span className="text-slate-400">#{e.seq}</span> {e.type} <span className="text-slate-500">{JSON.stringify(e.data).slice(0, 120)}</span>
                </li>
              ))}
            </ol>
          )}
        </Card>
        <Card title={`检查点（${hist.data?.length ?? "…"}）`} label="exec-checkpoints">
          <ErrorText error={hist.error} />
          <ol className="max-h-80 space-y-1 overflow-auto text-xs">
            {hist.data?.map((h) => (
              <li key={h.checkpoint_id} className="flex justify-between gap-2 border-b py-1">
                <span>
                  step {h.step} · {h.source}
                </span>
                <span className="text-slate-500">next: {h.next.join(",") || "∅"}</span>
              </li>
            ))}
          </ol>
        </Card>
      </div>
      <Card title="证据 / 工具 / 用量" label="exec-details">
        <RunDetails run={r} />
      </Card>
    </div>
  );
}
