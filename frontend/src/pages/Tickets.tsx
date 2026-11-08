import { type FormEvent, useState } from "react";
import { type Decision, type DraftRow, agent, platform } from "../api";
import {
  Badge,
  Card,
  ConfirmDialog,
  DRAFT_STATUS,
  EmptyState,
  ErrorText,
  Field,
  Loading,
  PageHeader,
  StatusBadge,
  TICKET_STATUS,
  Tabs,
  btn,
  btnPrimary,
  input,
  select,
  when,
} from "../components/ui";
import { errText } from "../errors";
import { useAsync } from "../lib/hooks";
import { Link, navigate } from "../lib/router";
import { useAuth } from "../lib/session";

export default function Tickets({ tab, number }: { tab: string; number?: string }) {
  const t = tab === "drafts" ? "drafts" : "list";
  return (
    <div>
      <PageHeader title="工单中心" subtitle="工单列表、状态流转、草稿审批与审计记录（全部真实持久化）" />
      <Tabs
        label="工单视图"
        value={t}
        onChange={(v) => navigate(v === "list" ? "/tickets" : "/tickets?tab=drafts")}
        items={[
          ["list", "工单列表"],
          ["drafts", "草稿与审批"],
        ]}
      />
      {t === "list" ? <TicketList number={number} /> : <Drafts />}
    </div>
  );
}

function TicketList({ number }: { number?: string }) {
  const { token } = useAuth();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [applied, setApplied] = useState({ q: "", status: "", priority: "" });
  const list = useAsync(() => platform.tickets(token, applied), [token, applied]);

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_26rem]">
      <Card title="工单" label="ticket-list">
        <form
          className="mb-4 flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setApplied({ q: q.trim(), status, priority });
          }}
        >
          <input aria-label="搜索工单" className={`${input} flex-1`} placeholder="编号 / 标题 / 服务" value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} />
          <select aria-label="工单状态" className={select} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">全部状态</option>
            {Object.entries(TICKET_STATUS).map(([k, [l]]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
          <select aria-label="优先级" className={select} value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="">全部优先级</option>
            {["P1", "P2", "P3", "P4"].map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
          <button className={btnPrimary} type="submit">
            查询
          </button>
        </form>
        <ErrorText error={list.error} />
        {list.loading && !list.data && <Loading />}
        {list.data?.length === 0 && <EmptyState title="没有符合条件的工单" />}
        {!!list.data?.length && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th className="py-1">编号</th>
                  <th>标题</th>
                  <th>优先级</th>
                  <th>状态</th>
                  <th>服务</th>
                  <th>创建时间</th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((t) => (
                  <tr key={t.number} className={`border-t ${t.number === number ? "bg-indigo-50" : ""}`}>
                    <td className="py-1.5 font-mono">
                      <Link to={`/tickets/${t.number}`} className="text-indigo-700 hover:underline">
                        {t.number}
                      </Link>
                    </td>
                    <td>{t.title}</td>
                    <td>
                      <Badge tone={t.priority === "P1" ? "red" : t.priority === "P2" ? "amber" : "slate"}>{t.priority}</Badge>
                    </td>
                    <td>
                      <StatusBadge map={TICKET_STATUS} value={t.status} />
                    </td>
                    <td className="text-xs">{t.service ?? "–"}</td>
                    <td className="text-xs text-slate-500">{when(t.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {number ? <TicketDetail number={number} onChanged={() => void list.reload()} /> : <EmptyState title="选择工单查看详情" />}
    </div>
  );
}

function TicketDetail({ number, onChanged }: { number: string; onChanged: () => void }) {
  const { token, can } = useAuth();
  const t = useAsync(() => platform.ticket(token, number), [token, number]);
  const tr = useAsync(() => platform.transitions(token), [token]);
  const [to, setTo] = useState("");
  const [note, setNote] = useState("");
  const [resolution, setResolution] = useState("");
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);

  if (t.error) return <ErrorText error={t.error} />;
  if (!t.data) return <Loading />;
  const tk = t.data;
  const next = tr.data?.[tk.status] ?? [];

  function validate(): boolean {
    const e: Record<string, string> = {};
    if (!to) e.to = "请选择目标状态";
    if (!note.trim()) e.note = "请填写处理说明";
    if (to === "resolved" && !resolution.trim()) e.resolution = "解决工单需要填写解决方案";
    setErrs(e);
    return !Object.keys(e).length;
  }
  async function apply() {
    setConfirm(false);
    setError(null);
    try {
      await platform.transition(token, number, { status: to, note: note.trim(), resolution: resolution.trim() || undefined });
      setTo("");
      setNote("");
      setResolution("");
      await t.reload();
      onChanged();
    } catch (e) {
      setError(errText(e));
    }
  }
  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    if (to === "closed") setConfirm(true);
    else void apply();
  }

  return (
    <Card title={`${tk.number} · ${tk.title}`} label="ticket-detail">
      <div className="space-y-4 text-sm">
        <div className="flex flex-wrap gap-2">
          <StatusBadge map={TICKET_STATUS} value={tk.status} />
          <Badge>{tk.priority}</Badge>
          <Badge tone="sky">{tk.service ?? "–"}</Badge>
          <Badge>{tk.category}</Badge>
        </div>
        <p className="whitespace-pre-wrap rounded bg-slate-50 p-3 text-xs text-slate-700">{tk.description}</p>
        {tk.resolution && <p className="text-xs text-emerald-700">解决方案：{tk.resolution}</p>}
        <section>
          <h3 className="mb-2 text-xs font-medium text-slate-500">审计时间线</h3>
          <ol className="space-y-2 border-l-2 border-slate-200 pl-3" aria-label="ticket-events">
            {tk.events.map((e, i) => (
              <li key={i} className="text-xs">
                <span className="font-medium">
                  {e.from_status ? `${TICKET_STATUS[e.from_status]?.[0] ?? e.from_status} → ` : "创建 → "}
                  {TICKET_STATUS[e.to_status]?.[0] ?? e.to_status}
                </span>{" "}
                <span className="text-slate-500">
                  {when(e.created_at)} · 操作人 {e.actor_name ?? e.actor_id?.slice(0, 8) ?? "–"}
                  {e.approval_id && ` · 审批 ${e.approval_id.slice(0, 8)}（${e.approver_name ?? "管理员"}）`}
                </span>
                {e.note && <p className="text-slate-600">{e.note}</p>}
              </li>
            ))}
          </ol>
        </section>
        {can("admin", "support") ? (
          next.length > 0 && (
            <form onSubmit={onSubmit} noValidate className="space-y-3 rounded-lg border border-slate-200 p-3" aria-label="状态流转">
              <Field label="目标状态" error={errs.to}>
                <select aria-label="目标状态" className={`${select} w-full`} value={to} onChange={(e) => setTo(e.target.value)}>
                  <option value="">请选择</option>
                  {next.map((s) => (
                    <option key={s} value={s}>
                      {TICKET_STATUS[s]?.[0] ?? s}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="处理说明" error={errs.note}>
                <input aria-label="处理说明" className={`${input} w-full`} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
              </Field>
              {to === "resolved" && (
                <Field label="解决方案" error={errs.resolution}>
                  <textarea aria-label="解决方案" className={`${input} w-full`} maxLength={2000} value={resolution} onChange={(e) => setResolution(e.target.value)} />
                </Field>
              )}
              <ErrorText error={error} />
              <button className={btnPrimary} type="submit">
                提交流转
              </button>
            </form>
          )
        ) : (
          <p className="text-xs text-slate-500">普通业务用户只能查看本人工单；状态流转由技术支持工程师或系统管理员执行。</p>
        )}
      </div>
      <ConfirmDialog open={confirm} title="关闭工单" body={`确认关闭 ${tk.number}？关闭后仅可重新打开为“处理中”。`} confirmText="关闭工单" onCancel={() => setConfirm(false)} onConfirm={() => void apply()} />
    </Card>
  );
}

function Drafts() {
  const { token, can } = useAuth();
  const [status, setStatus] = useState("pending_approval");
  const drafts = useAsync(() => platform.drafts(token, status || undefined), [token, status]);
  const [notice, setNotice] = useState<string | null>(null);
  return (
    <Card
      title="工单草稿"
      label="draft-list"
      actions={
        <select aria-label="草稿状态" className={select} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部</option>
          {Object.entries(DRAFT_STATUS).map(([k, [l]]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
      }
    >
      {!can("admin") && <p className="mb-3 text-xs text-slate-500">审批仅限系统管理员；你可以查看草稿状态与审批记录。</p>}
      {notice && (
        <p role="status" aria-label="审批结果" className="mb-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {notice}
        </p>
      )}
      <ErrorText error={drafts.error} />
      {drafts.loading && !drafts.data && <Loading />}
      {drafts.data?.length === 0 && <EmptyState title="没有该状态的草稿" />}
      <ul className="space-y-3">
        {drafts.data?.map((d) => (
          <DraftItem
            key={d.draft_id}
            d={d}
            onDone={(msg) => {
              setNotice(msg);
              void drafts.reload();
            }}
          />
        ))}
      </ul>
    </Card>
  );
}

function DraftItem({ d, onDone }: { d: DraftRow; onDone: (msg: string) => void }) {
  const { token, can } = useAuth();
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<Decision | null>(null);

  async function decide(decision: Decision) {
    setConfirm(null);
    if (decision === "returned" && !comment.trim()) return setError("退回修改必须填写意见");
    if (!d.run_id) return setError("该草稿没有关联运行，无法审批");
    setBusy(true);
    setError(null);
    try {
      const r = await agent.approve(token, d.run_id, decision, comment.trim());
      const label = { approved: "已批准", rejected: "已拒绝", returned: "已退回修改" }[decision];
      onDone(r.draft_result?.ticket_number ? `「${d.title}」${label}并创建工单 ${r.draft_result.ticket_number}` : `「${d.title}」${label}`);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-lg border border-slate-200 p-3 text-sm" aria-label={`草稿 ${d.title}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">{d.title}</span>
        <span className="flex gap-2">
          <StatusBadge map={DRAFT_STATUS} value={d.status} />
          <Badge>{d.priority}</Badge>
          <Badge tone="sky">r{d.revision}</Badge>
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        {d.action === "create" ? "新建工单" : "更新工单"} · 服务 {d.service ?? "–"} · 证据 {d.evidence_count} 条 · 提交人 {d.created_by} · {when(d.created_at)}
        {d.run_id && (
          <>
            {" · "}
            <Link to={`/executions/${d.run_id}`} className="text-indigo-600 underline">
              查看执行
            </Link>
          </>
        )}
      </p>
      {d.approval && (
        <p className="mt-2 rounded bg-slate-50 px-2 py-1 text-xs" aria-label="审批记录">
          审批记录：{d.approval.decision} · {d.approval.reviewer} · {when(d.approval.decided_at)} {d.approval.comment && `· “${d.approval.comment}”`}
        </p>
      )}
      {can("admin") && d.status === "pending_approval" && (
        <div className="mt-3 space-y-2">
          <input aria-label="审批意见" className={`${input} w-full`} placeholder="审批意见（退回修改时必填）" maxLength={500} value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <button className={btnPrimary} disabled={busy} onClick={() => void decide("approved")}>
              批准
            </button>
            <button className={btn} disabled={busy} onClick={() => void decide("returned")}>
              退回修改
            </button>
            <button className={btn} disabled={busy} onClick={() => setConfirm("rejected")}>
              拒绝
            </button>
          </div>
        </div>
      )}
      <ErrorText error={error} />
      <ConfirmDialog open={confirm === "rejected"} title="拒绝草稿" body={`拒绝后草稿「${d.title}」不会创建工单，此操作不可撤销。`} confirmText="拒绝" onCancel={() => setConfirm(null)} onConfirm={() => void decide("rejected")} />
    </li>
  );
}
