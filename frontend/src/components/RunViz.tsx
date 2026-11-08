// Execution visualization (no chart library): reference pipeline + executed sequence with
// per-node duration bars, current node, tool calls and approval waiting.
import type { AgentRun, TraceNode } from "../api";
import type { AgentEvent } from "../lib/sse";
import { Badge, EmptyState, RUN_STATUS, StatusBadge, type Tone } from "./ui";

export const PIPELINE: [string, string][] = [
  ["guard_in", "输入防护"],
  ["classify", "意图分类"],
  ["plan", "规划"],
  ["retrieve", "知识检索"],
  ["agent", "工具决策"],
  ["tools", "工具执行(MCP)"],
  ["collect", "结果汇总"],
  ["assess", "证据评估"],
  ["generate", "生成回答"],
  ["verify", "引用校验"],
  ["draft", "工单草稿"],
  ["approval", "人工审批"],
  ["apply_approval", "执行审批"],
  ["revise", "退回修改"],
  ["respond", "输出"],
  ["refuse", "拒答"],
  ["fail_safe", "安全兜底"],
];
const LABEL = Object.fromEntries(PIPELINE);

export function nodesFromEvents(events: AgentEvent[]): TraceNode[] {
  return events
    .filter((e) => e.type === "node")
    .map((e) => ({ turn: 0, node: String(e.data.node), status: String(e.data.status), ms: Number(e.data.ms ?? 0), detail: (e.data.detail as Record<string, unknown>) ?? {} }));
}

export function ExecutionGraph({
  trace,
  status,
  current,
  tools,
}: {
  trace: TraceNode[];
  status: string;
  current?: string | null;
  tools?: { tool: string; status: string; duration_ms: number | null }[];
}) {
  const visited = new Map<string, TraceNode>();
  for (const n of trace) visited.set(n.node, n);
  const waiting = status === "awaiting_approval";
  const tone = (node: string): string => {
    if (waiting && node === "approval") return "border-amber-500 bg-amber-50 text-amber-900 ring-2 ring-amber-300";
    if (current === node) return "border-sky-500 bg-sky-50 text-sky-900 ring-2 ring-sky-300 animate-pulse";
    const v = visited.get(node);
    if (!v) return "border-dashed border-slate-300 text-slate-400";
    return v.status === "ok" ? "border-emerald-500 bg-emerald-50 text-emerald-900" : "border-rose-500 bg-rose-50 text-rose-900";
  };
  const total = trace.reduce((a, n) => a + n.ms, 0) || 1;
  return (
    <div className="space-y-4" aria-label="execution-graph">
      <div>
        <p className="mb-2 text-xs text-slate-500">参考流水线（实线=已执行，虚线=未经过；蓝色=当前节点，琥珀=等待审批）</p>
        <ol className="flex flex-wrap items-center gap-1.5">
          {PIPELINE.map(([k, label], i) => (
            <li key={k} className="flex items-center gap-1.5">
              <span data-node={k} data-state={waiting && k === "approval" ? "waiting" : current === k ? "current" : visited.has(k) ? visited.get(k)!.status : "idle"} className={`rounded-md border px-2 py-1 text-xs ${tone(k)}`} title={k}>
                {label}
              </span>
              {i < PIPELINE.length - 1 && <span aria-hidden className="text-slate-300">→</span>}
            </li>
          ))}
        </ol>
      </div>
      <div>
        <p className="mb-2 text-xs text-slate-500">实际执行顺序与耗时（{trace.length} 个节点）</p>
        {trace.length === 0 ? (
          <EmptyState title="暂无节点记录" />
        ) : (
          <ol className="space-y-1" aria-label="node-timeline">
            {trace.map((n, i) => (
              <li key={`${n.node}-${i}`} className="grid grid-cols-[2rem_8rem_1fr_5rem] items-center gap-2 text-xs">
                <span className="text-slate-400">{i + 1}</span>
                <span className="truncate font-mono" title={n.node}>
                  {LABEL[n.node] ?? n.node}
                </span>
                <span className="h-2 rounded bg-slate-100">
                  <span className={`block h-2 rounded ${n.status === "ok" ? "bg-indigo-500" : "bg-rose-500"}`} style={{ width: `${Math.max(2, (n.ms / total) * 100)}%` }} />
                </span>
                <span className="text-right text-slate-500">
                  {n.ms.toFixed(1)} ms {n.status !== "ok" && <Badge tone="red">{n.status}</Badge>}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
      {tools && tools.length > 0 && (
        <div>
          <p className="mb-2 text-xs text-slate-500">工具调用</p>
          <ul className="flex flex-wrap gap-2" aria-label="tool-chips">
            {tools.map((t, i) => (
              <li key={i}>
                <Badge tone={(t.status === "ok" ? "green" : t.status === "denied" ? "slate" : "red") as Tone}>
                  {t.tool} · {t.status} · {t.duration_ms ?? 0}ms
                </Badge>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function RunDetails({ run }: { run: AgentRun }) {
  return (
    <div className="space-y-4 text-sm" aria-label="run-details">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge map={RUN_STATUS} value={run.status} />
        {run.is_mock && <Badge tone="amber">Mock 智能体（未调用真实大模型）</Badge>}
        <span className="text-xs text-slate-500">
          {run.llm} · {run.latency_ms ?? "–"} ms · tokens {run.usage.tokens_in}/{run.usage.tokens_out} · ¥{run.usage.cost_cny.toFixed(4)}
        </span>
      </div>
      {run.failure_reason && <p className="rounded bg-rose-50 px-3 py-2 text-xs text-rose-700">失败/中断原因：{run.failure_reason}</p>}
      <section>
        <h3 className="mb-1 text-xs font-medium text-slate-500">证据（{run.evidence.length}）</h3>
        {run.evidence.length === 0 ? (
          <p className="text-xs text-slate-400">无</p>
        ) : (
          <ul className="space-y-1" aria-label="evidence">
            {run.evidence.map((e) => (
              <li key={e.id} className="rounded border border-slate-200 p-2 text-xs">
                <Badge tone={e.source === "kb" ? "indigo" : "sky"}>{e.source === "kb" ? "知识库" : "工具"}</Badge> <strong>{e.title}</strong>{" "}
                <span className="text-slate-400">{e.locator}</span> {e.flagged && <Badge tone="red">可疑内容已隔离</Badge>}
                <p className="mt-1 text-slate-600">{e.snippet}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h3 className="mb-1 text-xs font-medium text-slate-500">工具轨迹（{run.tools.length}）</h3>
        {run.tools.length === 0 ? (
          <p className="text-xs text-slate-400">无</p>
        ) : (
          <table className="w-full text-xs" aria-label="tool-trace">
            <thead className="text-left text-slate-500">
              <tr>
                <th>工具</th>
                <th>状态</th>
                <th>耗时</th>
                <th>结果摘要</th>
              </tr>
            </thead>
            <tbody>
              {run.tools.map((t, i) => (
                <tr key={i} className="border-t align-top">
                  <td className="py-1 font-mono">{t.tool}</td>
                  <td>
                    <Badge tone={t.status === "ok" ? "green" : "red"}>{t.status}</Badge> {t.error_class}
                  </td>
                  <td>{t.duration_ms} ms</td>
                  <td className="max-w-xs truncate" title={t.result ?? ""}>
                    {t.result ?? "–"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
