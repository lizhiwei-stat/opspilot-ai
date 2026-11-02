import { type FormEvent, useState } from "react";
import { type AgentRun, type Decision, type McpAuditRow, type TicketView, type User, agent, mcp } from "../api";
import { Badge, Card, ErrorText, MockNotice, type Tone, btn, btnPrimary, input } from "../components/ui";
import { errText } from "../errors";

const STATUS: Record<AgentRun["status"], [string, Tone]> = {
  running: ["运行中", "amber"],
  awaiting_approval: ["等待审批", "amber"],
  completed: ["已完成", "green"],
  refused: ["已拒答", "red"],
  failed: ["失败", "red"],
  expired: ["已过期", "slate"],
};
const INCIDENT = "ORD-5003 错误，查文档和日志，分析并创建待审核工单";
const EXAMPLES = [
  "ORD-5003 是什么错误，怎么处理",
  "查一下工单 TCK-0001 的状态",
  "支付网关的商户号是多少",
  "订单接口 500 了，帮我创建一个工单",
  INCIDENT,
];

export default function Agent({ token, user }: { token: string; user: User | null }) {
  const [q, setQ] = useState(EXAMPLES[0]);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [thread, setThread] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [comment, setComment] = useState("");
  const [queue, setQueue] = useState<AgentRun[] | null>(null);
  const [ticket, setTicket] = useState<TicketView | null>(null);
  const [audit, setAudit] = useState<McpAuditRow[] | null>(null);

  async function exec(p: Promise<AgentRun>) {
    setBusy(true);
    setError(null);
    try {
      const r = await p;
      setRun(r);
      setThread(r.thread_id);
      setTicket(null);
      setAudit(null);
      setComment("");
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }
  const onAsk = (e: FormEvent) => {
    e.preventDefault();
    void exec(agent.run(token, q, thread));
  };

  async function side<T>(p: Promise<T>, set: (v: T) => void) {
    setError(null);
    try {
      set(await p);
    } catch (err) {
      setError(errText(err));
    }
  }
  const decide = (d: Decision) => run && void exec(agent.approve(token, run.run_id, d, comment));
  const turnTrace = run ? run.node_trace : [];
  const result = run?.draft_result;
  return (
    <div className="space-y-6">
      <Card
        title="Agent 工作台"
        label="agent"
        actions={
          <div className="flex items-center gap-2 text-xs text-slate-500">
            {thread ? <span title={thread}>会话 {thread.slice(0, 8)}…</span> : <span>新会话</span>}
            <button type="button" className={btn} onClick={() => { setThread(undefined); setRun(null); }}>
              新会话
            </button>
          </div>
        }
      >
        <form onSubmit={onAsk} className="flex gap-3">
          <input aria-label="Agent 问题" className={`${input} flex-1`} value={q} onChange={(e) => setQ(e.target.value)} />
          <button className={btnPrimary} disabled={busy || !q.trim()}>
            {busy ? "执行中…" : thread ? "继续追问" : "运行"}
          </button>
        </form>
        <div className="mt-2 flex flex-wrap gap-2 text-xs">
          {EXAMPLES.map((x) => (
            <button key={x} type="button" className="rounded-full bg-slate-100 px-2 py-0.5 hover:bg-slate-200" onClick={() => setQ(x)}>
              {x}
            </button>
          ))}
        </div>
        <div className="mt-3">
          <ErrorText error={error} />
        </div>
      </Card>

      {user?.role === "admin" && (
        <Card
          label="approval-queue"
          title="待审批队列"
          actions={
            <button type="button" className={btn} onClick={() => void side(agent.approvals(token), setQueue)}>
              刷新队列
            </button>
          }
        >
          {queue === null ? (
            <p className="text-xs text-slate-500">点击“刷新队列”加载所有等待审批的运行（重启后仍可恢复）。</p>
          ) : queue.length === 0 ? (
            <p className="text-sm text-slate-500">暂无待审批</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {queue.map((r) => (
                <li key={r.run_id} className="flex items-center justify-between gap-2">
                  <span className="truncate">
                    {r.draft?.title ?? r.question} · 第 {r.draft?.revision ?? 1} 版
                  </span>
                  <button
                    type="button"
                    className={btn}
                    onClick={() => {
                      setRun(r);
                      setThread(r.thread_id);
                      setTicket(null);
                      setAudit(null);
                    }}
                  >
                    打开 {r.run_id.slice(0, 8)}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {run && (
        <>
          <Card
            label="agent-result"
            title={`第 ${run.turn} 轮结果`}
            actions={
              <div className="flex flex-wrap gap-2">
                <Badge tone={STATUS[run.status][1]}>{STATUS[run.status][0]}</Badge>
                <Badge tone={run.is_mock ? "amber" : "indigo"}>{run.is_mock ? "Mock 模型" : run.llm}</Badge>
                {run.intent && <Badge tone="sky">意图：{run.intent}</Badge>}
              </div>
            }
          >
            {run.is_mock && (
              <div className="mb-3">
                <MockNotice>
                  Mock 智能体：用确定性规则模拟模型的分类、工具选择和摘录式生成，未调用任何大模型。这里只验证流程、权限、工具和引用核验，不代表真实模型的推理能力。
                </MockNotice>
              </div>
            )}
            <pre
              aria-label="最终回答"
              className={`whitespace-pre-wrap rounded-lg p-4 font-sans text-sm ${
                run.status === "refused" || run.status === "failed" ? "bg-rose-50 text-rose-800" : "bg-slate-50"
              }`}
            >
              {run.answer ?? "（无）"}
            </pre>
            {run.citations.length > 0 && (
              <ol className="mt-3 space-y-1 text-xs text-slate-600" aria-label="引用">
                {run.citations.map((c) => (
                  <li key={c.index}>
                    [{c.index}] {c.source === "tool" ? "工具" : "文档"} · {c.title} {c.locator && `· ${c.locator}`}
                  </li>
                ))}
              </ol>
            )}
            {(run.refusal_reason || run.failure_reason) && (
              <p className="mt-3 text-xs text-rose-700">
                原因：{run.refusal_reason ?? run.failure_reason}
                {run.assessment.missing && run.assessment.missing.length > 0 && ` · 缺少：${run.assessment.missing.join("；")}`}
              </p>
            )}
            {run.policy_violations.length > 0 && <p className="mt-1 text-xs text-rose-700">核验未通过：{run.policy_violations.join("，")}</p>}
            {run.degraded.length > 0 && <p className="mt-1 text-xs text-amber-700">降级/恢复：{run.degraded.join("，")}</p>}
            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1 border-t pt-3 text-xs text-slate-500 md:grid-cols-4">
              <dt>输入 tokens</dt>
              <dd aria-label="tokens-in">{run.usage.tokens_in}</dd>
              <dt>输出 tokens</dt>
              <dd>{run.usage.tokens_out}</dd>
              <dt>费用</dt>
              <dd>¥{run.usage.cost_cny.toFixed(6)}</dd>
              <dt>延迟</dt>
              <dd aria-label="latency">{run.latency_ms ?? "–"} ms</dd>
              <dt className="col-span-2 md:col-span-4 text-[11px]">token 来源：{run.usage.source}</dt>
            </dl>
          </Card>

          {run.status === "awaiting_approval" && run.draft && (
            <Card label="approval" title="人工审批（工单草稿）">
              <p className="text-sm">
                草稿「{run.draft.title}」· 优先级 {run.draft.priority} · 服务 {run.draft.service ?? "-"} · 状态 {run.draft.status} · 第{" "}
                {run.draft.revision ?? 1} 版
              </p>
              {run.draft.description && (
                <pre aria-label="草稿内容" className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 font-sans text-xs">
                  {run.draft.description}
                </pre>
              )}
              {user?.role === "admin" ? (
                <div className="mt-3 space-y-2">
                  <textarea
                    aria-label="审批意见"
                    className={`${input} w-full`}
                    rows={2}
                    placeholder="审批意见（退回修改时必填）"
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                  />
                  <div className="flex gap-2">
                    <button className={btnPrimary} disabled={busy} onClick={() => decide("approved")}>
                      批准
                    </button>
                    <button className={btn} disabled={busy} onClick={() => decide("rejected")}>
                      驳回
                    </button>
                    <button className={btn} disabled={busy || !comment.trim()} onClick={() => decide("returned")}>
                      退回修改
                    </button>
                  </div>
                  <p className="text-[11px] text-slate-400">审批由服务端校验管理员身份；对话或模型输出中的“已批准”文字不构成审批。</p>
                </div>
              ) : (
                <p className="mt-2 text-xs text-slate-500">需要管理员审批。管理员登录后可在此页打开该运行（run {run.run_id.slice(0, 8)}…）。</p>
              )}
            </Card>
          )}
          {run.draft && run.status !== "awaiting_approval" && (
            <p className="text-xs text-slate-600" aria-label="draft-status">工单草稿状态：{run.draft.status}</p>
          )}
          {result && (
            <Card label="ticket-result" title="审批结果与业务操作">
              <p className="text-sm" aria-label="approval-result">
                决定：{result.decision ?? "-"} · 审批时间 {result.decided_at ?? "-"} · 草稿第 {result.revision ?? 1} 版
                {result.ticket_number && (
                  <>
                    {" "}· 已创建工单 <span className="font-mono">{result.ticket_number}</span>
                  </>
                )}
              </p>
              {result.ticket_number && (
                <button type="button" className={`${btn} mt-2`} onClick={() => void side(mcp.ticket(token, result.ticket_number as string), setTicket)}>
                  查看工单与状态流转
                </button>
              )}
              {ticket && (
                <div className="mt-2 text-xs" aria-label="ticket-view">
                  <p>
                    {ticket.number} · {ticket.title} · {ticket.priority} · 状态 {ticket.status}
                  </p>
                  <ul className="mt-1 list-disc pl-5">
                    {ticket.events.map((e, i) => (
                      <li key={i}>
                        {e.from_status ?? "∅"} → {e.to_status} · 审批人 {e.approver_id?.slice(0, 8) ?? "-"} · {e.approved_at ?? e.created_at}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>
          )}

          <div className="grid gap-6 md:grid-cols-2">
            <Card label="node-trace" title="节点顺序">
              <ol className="space-y-1 text-sm">
                {turnTrace.map((n, i) => (
                  <li key={`${n.node}-${i}`} className="flex items-center justify-between gap-2">
                    <span className="font-mono">
                      {i + 1}. {n.node}
                    </span>
                    <span className="truncate text-xs text-slate-500" title={JSON.stringify(n.detail)}>
                      {summarize(n.detail)}
                    </span>
                    <Badge tone={n.status === "ok" ? "green" : "red"}>{n.ms.toFixed(1)}ms</Badge>
                  </li>
                ))}
              </ol>
              <p className="mt-3 text-[11px] text-slate-400">仅展示系统记录的节点与决策摘要，不展示模型内部推理过程。</p>
            </Card>
            <Card label="tool-calls" title={`工具调用（${run.tools.length}）`}>
              {run.tools.length === 0 ? (
                <p className="text-sm text-slate-500">本轮未调用工具</p>
              ) : (
                <ul className="space-y-2 text-sm">
                  {run.tools.map((t, i) => (
                    <li key={i} className="rounded-md border p-2">
                      <div className="flex items-center justify-between">
                        <span className="font-mono">{t.tool}</span>
                        <Badge tone={t.status === "ok" ? "green" : "red"}>
                          {t.status}
                          {t.error_class ? ` · ${t.error_class}` : ""} · 第{t.attempt}次 · {t.duration_ms}ms
                        </Badge>
                      </div>
                      <p className="mt-1 truncate font-mono text-xs text-slate-500">参数：{JSON.stringify(t.args)}</p>
                      {t.result && <p className="mt-1 text-xs text-slate-600">结果：{t.result}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <Card
            label="mcp-audit"
            title="MCP 审计（本会话）"
            actions={
              <button type="button" className={btn} onClick={() => void side(mcp.audit(token, run.thread_id), setAudit)}>
                加载审计
              </button>
            }
          >
            {audit === null ? (
              <p className="text-xs text-slate-500">服务端与客户端两侧的 MCP 调用记录（参数已脱敏，仅摘要与哈希）。</p>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-slate-500">
                    <th>侧</th>
                    <th>服务</th>
                    <th>工具</th>
                    <th>状态</th>
                    <th>耗时</th>
                    <th>错误</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((a, i) => (
                    <tr key={i} className="border-t">
                      <td>{a.side}</td>
                      <td>{a.server}</td>
                      <td className="font-mono">{a.tool}</td>
                      <td>{a.status}</td>
                      <td>{a.duration_ms ?? "-"}ms</td>
                      <td className="truncate">{a.error ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          <Card label="agent-evidence" title={`证据（${run.evidence.length}）· 判定：${run.assessment.decision ?? "-"}`}>
            {run.assessment.reasons && <p className="mb-2 text-xs text-slate-500">依据：{run.assessment.reasons.join("，")}</p>}
            {run.assessment.contradictions && run.assessment.contradictions.length > 0 && (
              <p className="mb-2 text-xs text-amber-700">矛盾：{run.assessment.contradictions.join("；")}</p>
            )}
            <ul className="space-y-2 text-sm">
              {run.evidence.map((e) => (
                <li key={e.id} className="rounded-md bg-slate-50 p-2">
                  <div className="flex items-center gap-2 text-xs text-slate-500">
                    <Badge tone={e.source === "tool" ? "sky" : "slate"}>{e.source === "tool" ? "工具" : "知识库"}</Badge>
                    <span>{e.title}</span>
                    {e.flagged && <Badge tone="red">疑似注入（已剔除）</Badge>}
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs">{e.snippet}</p>
                </li>
              ))}
            </ul>
          </Card>
        </>
      )}
    </div>
  );
}

function summarize(d: Record<string, unknown>): string {
  const keys = ["decision", "intent", "route", "next", "accepted", "calls", "status", "reason", "error", "draft"];
  return keys
    .filter((k) => d[k] !== undefined && d[k] !== null)
    .map((k) => `${k}=${JSON.stringify(d[k])}`)
    .join(" ")
    .slice(0, 80);
}
