import { platform } from "../api";
import { Badge, Card, ErrorText, Loading, PageHeader, btn } from "../components/ui";
import { useAsync } from "../lib/hooks";
import { useAuth } from "../lib/session";

export default function Settings() {
  const { token } = useAuth();
  const s = useAsync(() => platform.settings(token), [token]);
  return (
    <div>
      <PageHeader
        title="系统设置"
        subtitle="运行时配置只读展示；密钥只显示“是否已配置”，任何接口都不会返回明文"
        actions={
          <button className={btn} onClick={() => void s.reload()}>
            刷新
          </button>
        }
      />
      <ErrorText error={s.error} />
      {!s.data && !s.error && <Loading />}
      {s.data && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="模型" label="settings-models">
            <KV obj={s.data.models} />
          </Card>
          <Card title="运行时" label="settings-runtime">
            <KV obj={s.data.runtime} />
          </Card>
          <Card title="预算与付费调用" label="settings-budget">
            <dl className="grid grid-cols-2 gap-y-1 text-sm">
              <dt className="text-slate-500">付费接口</dt>
              <dd>{s.data.budget.allow_paid_api ? <Badge tone="red">已开启</Badge> : <Badge tone="green">关闭（默认）</Badge>}</dd>
              <dt className="text-slate-500">单轮预算上限</dt>
              <dd>¥{s.data.budget.paid_budget_cny}</dd>
              <dt className="text-slate-500">单轮最大调用</dt>
              <dd>{s.data.budget.paid_max_calls}</dd>
              <dt className="text-slate-500">授权批次</dt>
              <dd>{s.data.budget.paid_approval_configured ? "已配置" : "未配置"}</dd>
              <dt className="text-slate-500">账本记录</dt>
              <dd>
                {s.data.budget.ledger.entries ?? "不可用"} 条 · ¥{s.data.budget.ledger.cost_cny ?? "–"}
              </dd>
            </dl>
          </Card>
          <Card title={`健康检查 · ${s.data.health.status === "ready" ? "就绪" : "未就绪"}`} label="settings-health">
            <ul className="space-y-1 text-sm">
              {s.data.health.checks.map((c) => (
                <li key={c.name} className="flex justify-between gap-2">
                  <span className="font-mono">{c.name}</span>
                  <span className="truncate text-xs text-slate-500" title={c.detail}>
                    {c.detail}
                  </span>
                  <Badge tone={c.ok ? "green" : "red"}>{c.ok ? `${c.latency_ms}ms` : "失败"}</Badge>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-slate-500">
              v{s.data.version} · Stage {s.data.stage} · {s.data.env}
            </p>
          </Card>
        </div>
      )}
    </div>
  );
}

function KV({ obj }: { obj: Record<string, string | number | boolean> }) {
  return (
    <dl className="grid grid-cols-2 gap-y-1 text-sm">
      {Object.entries(obj).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="font-mono text-xs text-slate-500">{k}</dt>
          <dd>{typeof v === "boolean" ? <Badge tone={v ? "green" : "slate"}>{v ? "是" : "否"}</Badge> : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}
