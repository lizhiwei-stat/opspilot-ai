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
