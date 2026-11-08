import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { type AgentRun, agent } from "../api";
import { ExecutionGraph, RunDetails, nodesFromEvents } from "../components/RunViz";
import { Badge, Card, EmptyState, ErrorText, Loading, MockNotice, RUN_STATUS, StatusBadge, btn, btnDanger, btnPrimary, input, when } from "../components/ui";
import { errText } from "../errors";
import { useAsync } from "../lib/hooks";
import { Link, navigate } from "../lib/router";
import { useAuth } from "../lib/session";
import { type AgentEvent, type StreamState, streamRunEvents } from "../lib/sse";

const MAX_Q = 1000;

interface Live {
  runId: string;
  events: AgentEvent[];
  state: StreamState;
  reconnects: number;
}

export default function Workspace({ threadId }: { threadId?: string }) {
  const { token, user } = useAuth();
  const threads = useAsync(() => agent.threads(token), [token]);
  const runs = useAsync(() => (threadId ? agent.thread(token, threadId) : Promise.resolve([] as AgentRun[])), [token, threadId]);
  const [q, setQ] = useState("");
  const [qErr, setQErr] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState<Live | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  // stream callbacks outlive renders: always reload through the latest hook instances
  const reloadRef = useRef({ runs, threads });
  useEffect(() => {
    reloadRef.current = { runs, threads };
  });

  const list = runs.data ?? [];
  const sel = list.find((r) => r.run_id === selected) ?? list[list.length - 1] ?? null;

  const follow = useCallback(
    (runId: string, after = 0, keep: AgentEvent[] = []) => {
      stopRef.current?.();
      setLive({ runId, events: keep, state: { kind: "connecting", attempt: 1 }, reconnects: 0 });
      stopRef.current = streamRunEvents({
        token,
        runId,
        after,
        onEvent: (e) => setLive((l) => (l && l.runId === runId ? { ...l, events: [...l.events, e] } : l)),
        onState: (s) => {
          setLive((l) => (l && l.runId === runId ? { ...l, state: s, reconnects: l.reconnects + (s.kind === "reconnecting" ? 1 : 0) } : l));
          if (s.kind === "ended") {
            void reloadRef.current.runs.reload();
            void reloadRef.current.threads.reload();
          }
        },
      });
    },
    [token],
  );

  useEffect(() => () => stopRef.current?.(), []);

  // After a page refresh: re-attach to a run that is still executing (replay from seq 0).
  useEffect(() => {
    const last = list[list.length - 1];
    if (last && last.status === "running" && live?.runId !== last.run_id) follow(last.run_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs.data]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const text = q.trim();
    if (!text) return setQErr("请输入问题");
    if (text.length > MAX_Q) return setQErr(`问题不能超过 ${MAX_Q} 字`);
    setQErr(null);
    setError(null);
    setBusy(true);
    try {
      const r = await agent.run(token, text, threadId, true);
      setQ("");
      setSelected(r.run_id);
      runs.setData([...(threadId ? list : []), r]);
      follow(r.run_id);
      if (!threadId) navigate(`/workspace/${r.thread_id}`);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  async function cancel(runId: string) {
    try {
      await agent.cancel(token, runId);
    } catch (err) {
      setError(errText(err));
    }
  }

  async function resume(r: AgentRun) {
    setError(null);
    try {
      const keep = live?.runId === r.run_id ? live.events : [];
      await agent.resume(token, r.run_id, true);
      void runs.reload();
      follow(r.run_id, keep.length ? keep[keep.length - 1].seq : 0, keep);
    } catch (err) {
      setError(errText(err));
    }
  }

  const liveForSel = live && sel && live.runId === sel.run_id ? live : null;
  const running = sel?.status === "running" || (liveForSel && !["ended", "failed"].includes(liveForSel.state.kind));
  const liveNodes = liveForSel ? nodesFromEvents(liveForSel.events) : [];
  const lastNode = liveNodes[liveNodes.length - 1]?.node ?? null;

  return (
    <div className="grid gap-6 xl:grid-cols-[16rem_1fr_24rem]">
      <aside aria-label="会话列表" className="space-y-2">
        <button className={`${btnPrimary} w-full`} onClick={() => navigate("/workspace")}>
          ＋ 新建会话
        </button>
        {threads.loading && !threads.data && <Loading />}
        <ErrorText error={threads.error} />
        {threads.data?.length === 0 && <EmptyState title="暂无历史会话" />}
        <ul className="space-y-1">
          {threads.data?.map((t) => (
            <li key={t.thread_id}>
              <Link
                to={`/workspace/${t.thread_id}`}
                aria-current={t.thread_id === threadId ? "page" : undefined}
                className={`block rounded-md border px-3 py-2 text-sm ${t.thread_id === threadId ? "border-indigo-400 bg-indigo-50" : "border-slate-200 bg-white hover:bg-slate-50"}`}
              >
                <span className="line-clamp-1 font-medium">{t.title}</span>
                <span className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
                  <StatusBadge map={RUN_STATUS} value={t.last_status} /> {t.turns} 轮 · {when(t.updated_at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </aside>

      <section className="min-w-0 space-y-4" aria-label="对话">
        <MockNotice>
          流式说明：这里推送的是<strong>节点 / 工具 / 证据级事件</strong>（SSE，可断线续传），最终回答在运行结束后一次性返回；不是逐 token 流式输出，也不展示模型的私有推理过程。
        </MockNotice>
        <ErrorText error={error ?? runs.error} />
        {!threadId && list.length === 0 && <EmptyState title="开始一个新会话" hint="例如：ORD-5003 是什么错误？或：订单超时影响下单，帮我创建一个工单" />}
        {runs.loading && threadId && !runs.data && <Loading />}
        <ol className="space-y-4" aria-label="消息">
          {list.map((r) => (
            <li key={r.run_id} className="space-y-2">
              <div className="flex justify-end">
                <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2 text-sm text-white">{r.question}</p>
              </div>
              <button
                onClick={() => setSelected(r.run_id)}
                aria-label={`第 ${r.turn} 轮回答`}
                className={`block w-full rounded-2xl rounded-bl-sm border bg-white px-4 py-3 text-left text-sm shadow-sm ${sel?.run_id === r.run_id ? "border-indigo-400" : "border-slate-200"}`}
              >
                <span className="mb-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <StatusBadge map={RUN_STATUS} value={r.status} /> 第 {r.turn} 轮 · {r.latency_ms ?? "–"} ms {r.is_mock && <Badge tone="amber">Mock</Badge>}
                </span>
                {r.status === "running" ? (
                  <span className="text-slate-500">正在执行… 进度见右侧事件流</span>
                ) : (
                  <span className="whitespace-pre-wrap" aria-label="回答内容">
                    {r.answer ?? r.refusal_reason ?? r.failure_reason ?? "（无回答）"}
                  </span>
                )}
                {r.citations?.length > 0 && (
                  <span className="mt-2 block text-xs text-slate-500" aria-label="引用">
                    引用：{r.citations.map((c) => `[${c.index}] ${c.title}`).join("；")}
                  </span>
                )}
                {r.status === "awaiting_approval" && r.draft && (
                  <span className="mt-2 block rounded bg-amber-50 p-2 text-xs text-amber-900" aria-label="待审批草稿">
                    已生成工单草稿「{r.draft.title}」（{r.draft.priority}），等待系统管理员审批。
                    {user.role === "admin" && " 可在 工单中心 → 草稿与审批 中处理。"}
                  </span>
                )}
                {r.draft_result?.ticket_number && (
                  <span className="mt-2 block text-xs text-emerald-700">
                    审批结果：{r.draft_result.decision} · 工单 {r.draft_result.ticket_number}
                  </span>
                )}
              </button>
              {r.status === "aborted" && (
                <button className={btn} onClick={() => void resume(r)}>
                  从检查点继续执行
                </button>
              )}
            </li>
          ))}
        </ol>
        <form onSubmit={submit} noValidate className="sticky bottom-0 rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <label htmlFor="ask" className="sr-only">
            输入问题
          </label>
          <textarea
            id="ask"
            aria-label="输入问题"
            aria-invalid={!!qErr}
            rows={2}
            className={`${input} w-full resize-none`}
            placeholder={threadId ? "继续追问（同一会话保留上下文）" : "描述你的问题…"}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void submit(e as unknown as FormEvent);
            }}
          />
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className={`text-xs ${q.length > MAX_Q ? "text-rose-600" : "text-slate-400"}`}>
              {qErr ? <span role="alert" className="text-rose-600">{qErr}</span> : `${q.length}/${MAX_Q} · Ctrl+Enter 发送`}
            </span>
            <div className="flex gap-2">
              {running && sel && (
                <button type="button" className={btnDanger} onClick={() => void cancel(sel.run_id)}>
                  中断
                </button>
              )}
              <button type="submit" className={btnPrimary} disabled={busy || !!running}>
                发送
              </button>
            </div>
          </div>
        </form>
      </section>

      <aside className="min-w-0 space-y-4" aria-label="执行详情">
        <Card
          title="实时事件流"
          label="event-stream"
          actions={liveForSel && <StreamBadge s={liveForSel.state} reconnects={liveForSel.reconnects} />}
        >
          {!liveForSel ? (
            sel ? (
              <button className={btn} onClick={() => follow(sel.run_id)}>
                回放该轮事件
              </button>
            ) : (
              <p className="text-xs text-slate-400">发送问题后显示节点进度</p>
            )
          ) : (
            <>
              {lastNode && running && (
                <p className="mb-2 text-xs" aria-label="当前节点">
                  最新完成节点：<strong className="font-mono">{lastNode}</strong>
                </p>
              )}
              <ol className="max-h-72 space-y-1 overflow-auto font-mono text-[11px]" aria-label="事件列表">
                {liveForSel.events.map((e) => (
                  <li key={e.seq} data-seq={e.seq} className="flex gap-2">
                    <span className="text-slate-400">#{e.seq}</span>
                    <span className={e.type === "final" ? "font-semibold text-emerald-700" : e.type === "interrupt" ? "text-amber-700" : ""}>{e.type}</span>
                    <span className="truncate text-slate-500">{summarize(e)}</span>
                  </li>
                ))}
              </ol>
            </>
          )}
        </Card>
        {sel && (
          <Card title="执行可视化" label="workspace-graph" actions={<Link to={`/executions/${sel.run_id}`} className="text-xs text-indigo-600 underline">详情</Link>}>
            <ExecutionGraph trace={liveNodes.length && running ? liveNodes : sel.node_trace} status={sel.status} current={running ? lastNode : null} tools={sel.tools} />
          </Card>
        )}
        {sel && sel.status !== "running" && (
          <Card title="证据与工具" label="workspace-details">
            <RunDetails run={sel} />
          </Card>
        )}
      </aside>
    </div>
  );
}

function StreamBadge({ s, reconnects }: { s: StreamState; reconnects: number }) {
  const label =
    s.kind === "open" ? "已连接" : s.kind === "connecting" ? "连接中" : s.kind === "reconnecting" ? `重连中（第 ${s.attempt} 次，${Math.round(s.delayMs / 1000)}s）` : s.kind === "ended" ? "已结束" : `已断开：${s.reason}`;
  return (
    <span aria-label="stream-state" data-state={s.kind} className="text-xs">
      <Badge tone={s.kind === "open" ? "green" : s.kind === "ended" ? "slate" : s.kind === "failed" ? "red" : "amber"}>{label}</Badge>
      {reconnects > 0 && <span className="ml-1 text-slate-500">已重连 {reconnects} 次</span>}
    </span>
  );
}

function summarize(e: AgentEvent): string {
  const d = e.data;
  if (e.type === "node") return `${d.node} ${d.status} ${d.ms}ms`;
  if (e.type === "tool") return `${d.tool} ${d.status} ${d.duration_ms ?? 0}ms`;
  if (e.type === "evidence") return `${d.count} 条证据`;
  if (e.type === "final") return `状态 ${d.status}${d.error ? ` · ${d.error}` : ""}`;
  if (e.type === "run_started") return String(d.kind ?? "");
  if (e.type === "interrupt") return "等待人工审批";
  return "";
}
