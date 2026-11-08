import type { ReactNode } from "react";

export function Badge({ tone = "slate", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>
      {children}
    </span>
  );
}
export type Tone = "slate" | "green" | "red" | "amber" | "indigo" | "sky";
const TONES: Record<Tone, string> = {
  slate: "bg-slate-100 text-slate-700",
  green: "bg-emerald-100 text-emerald-700",
  red: "bg-rose-100 text-rose-700",
  amber: "bg-amber-100 text-amber-800",
  indigo: "bg-indigo-100 text-indigo-700",
  sky: "bg-sky-100 text-sky-700",
};

export const STATUS_TONE: Record<string, Tone> = {
  indexed: "green",
  failed: "red",
  indexing: "amber",
  pending: "amber",
  superseded: "slate",
  duplicate: "sky",
};
export const STATUS_LABEL: Record<string, string> = {
  indexed: "已索引",
  failed: "失败",
  indexing: "索引中",
  pending: "待处理",
  superseded: "已被新版本替换",
  duplicate: "重复内容",
};

export function Card({ title, actions, children, label }: { title: ReactNode; actions?: ReactNode; children: ReactNode; label?: string }) {
  return (
    <section aria-label={label} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="font-semibold">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function ErrorText({ error }: { error: string | null }) {
  return error ? (
    <p role="alert" className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">
      {error}
    </p>
  ) : null;
}

export function MockNotice({ children }: { children: ReactNode }) {
  return (
    <div role="note" className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
      {children}
    </div>
  );
}

export const fmt = (x: number | null | undefined, d = 3) => (x === null || x === undefined ? "–" : x.toFixed(d));

export const btn = "rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50";
export const btnPrimary = "rounded-md bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50";
export const input = "rounded-md border border-slate-300 px-3 py-2 text-sm";

// ---------------------------------------------------------------- Stage 5 shared components
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}

export function Loading({ label = "加载中…" }: { label?: string }) {
  return (
    <p role="status" aria-live="polite" className="flex items-center gap-2 py-6 text-sm text-slate-500">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" aria-hidden />
      {label}
    </p>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div role="note" aria-label="empty-state" className="rounded-lg border border-dashed border-slate-300 px-4 py-8 text-center">
      <p className="text-sm font-medium text-slate-600">{title}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

export function Stat({ label, value, sub, tone = "slate" }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm" aria-label={`stat-${label}`}>
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${tone === "red" ? "text-rose-600" : tone === "green" ? "text-emerald-600" : "text-slate-900"}`}>{value}</p>
      {sub && <p className="mt-1 text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, items, label }: { value: T; onChange: (v: T) => void; items: [T, string][]; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="mb-4 flex flex-wrap gap-1 border-b border-slate-200">
      {items.map(([k, text]) => (
        <button
          key={k}
          role="tab"
          aria-selected={value === k}
          onClick={() => onChange(k)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm ${value === k ? "border-indigo-600 font-medium text-indigo-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, error, hint, children }: { label: string; error?: string | null; hint?: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="text-slate-700">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && !error && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
      {error && (
        <span role="alert" className="mt-1 block text-xs text-rose-600">
          {error}
        </span>
      )}
    </label>
  );
}

/** Modal confirmation for destructive actions (focus lands on Cancel). */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmText = "确认",
  danger = true,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onKeyDown={(e) => e.key === "Escape" && onCancel()}>
      <div role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
        <h2 id="confirm-title" className="font-semibold">
          {title}
        </h2>
        <div className="mt-2 text-sm text-slate-600">{body}</div>
        <div className="mt-5 flex justify-end gap-2">
          <button autoFocus className={btn} onClick={onCancel}>
            取消
          </button>
          <button className={danger ? btnDanger : btnPrimary} onClick={onConfirm}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}

export const btnDanger = "rounded-md bg-rose-600 px-3 py-1.5 text-sm text-white hover:bg-rose-700 disabled:opacity-50";
export const select = "rounded-md border border-slate-300 bg-white px-2 py-2 text-sm";

export const RUN_STATUS: Record<string, [string, Tone]> = {
  running: ["运行中", "sky"],
  awaiting_approval: ["待审批", "amber"],
  completed: ["已完成", "green"],
  refused: ["已拒答", "slate"],
  failed: ["失败", "red"],
  expired: ["已过期", "slate"],
  aborted: ["已中断", "amber"],
};
export const TICKET_STATUS: Record<string, [string, Tone]> = {
  open: ["待处理", "amber"],
  in_progress: ["处理中", "sky"],
  resolved: ["已解决", "green"],
  closed: ["已关闭", "slate"],
};
export const DRAFT_STATUS: Record<string, [string, Tone]> = {
  pending_approval: ["待审批", "amber"],
  approved: ["已批准", "green"],
  executed: ["已执行", "green"],
  rejected: ["已拒绝", "red"],
  returned: ["已退回修改", "sky"],
  expired: ["已过期", "slate"],
};
export function StatusBadge({ map, value }: { map: Record<string, [string, Tone]>; value: string }) {
  const [label, tone] = map[value] ?? [value, "slate" as Tone];
  return <Badge tone={tone}>{label}</Badge>;
}
export const pct = (x: number | null | undefined) => (x === null || x === undefined ? "不可用" : `${(x * 100).toFixed(1)}%`);
export const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString("zh-CN", { hour12: false }) : "–");
