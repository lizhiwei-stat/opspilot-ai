import { type FormEvent, useEffect, useState } from "react";
import { type KbPermissions, api, platform, rag } from "../api";
import { Badge, Card, ConfirmDialog, EmptyState, ErrorText, Field, Loading, PageHeader, Tabs, btn, btnPrimary, input, select } from "../components/ui";
import { errText } from "../errors";
import { useAsync } from "../lib/hooks";
import { navigate } from "../lib/router";
import { useAuth } from "../lib/session";
import Knowledge from "./Knowledge";
import QA from "./QA";
import SearchDebug from "./SearchDebug";

type T = "docs" | "search" | "qa" | "manage";

export default function KnowledgeCenter({ tab }: { tab: string }) {
  const { token } = useAuth();
  const t = (["docs", "search", "qa", "manage"].includes(tab) ? tab : "docs") as T;
  const [reloadKey, setReloadKey] = useState(0);
  const [embedding, setEmbedding] = useState<string | undefined>();
  useEffect(() => {
    api.version().then((v) => setEmbedding(v.embedding_provider)).catch(() => setEmbedding(undefined));
  }, []);
  return (
    <div>
      <PageHeader title="知识库" subtitle="复用 Stage 2/2.5 RAG：混合检索 + 重排 + 引用；权限在数据库查询层强制执行" />
      <Tabs
        label="知识库视图"
        value={t}
        onChange={(v) => navigate(v === "docs" ? "/knowledge" : `/knowledge?tab=${v}`)}
        items={[
          ["docs", "文档管理"],
          ["search", "检索测试"],
          ["qa", "问答与引用"],
          ["manage", "知识库与权限"],
        ]}
      />
      {t === "docs" && <Knowledge token={token} reloadKey={reloadKey} />}
      {t === "search" && <SearchDebug token={token} embeddingProvider={embedding} />}
      {t === "qa" && <QA token={token} />}
      {t === "manage" && <Manage onChanged={() => setReloadKey((k) => k + 1)} />}
    </div>
  );
}

const SLUG = /^[a-z0-9][a-z0-9-]{1,62}$/;

function Manage({ onChanged }: { onChanged: () => void }) {
  const { token, can } = useAuth();
  const kbs = useAsync(() => rag.kbs(token), [token]);
  const [kbId, setKbId] = useState<string | null>(null);
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div className="space-y-6">
        {can("admin", "support") ? (
          <CreateKb
            onCreated={(id) => {
              void kbs.reload();
              setKbId(id);
              onChanged();
            }}
          />
        ) : (
          <Card title="新建知识库">
            <p className="text-sm text-slate-500">普通业务用户不能创建知识库（后端返回 403）。</p>
          </Card>
        )}
        <Card title="可访问的知识库" label="kb-manage-list">
          <ErrorText error={kbs.error} />
          {kbs.loading && !kbs.data && <Loading />}
          <ul className="space-y-1">
            {kbs.data?.map((k) => (
              <li key={k.id}>
                <button onClick={() => setKbId(k.id)} className={`w-full rounded-md px-3 py-2 text-left text-sm ${k.id === kbId ? "bg-indigo-50" : "hover:bg-slate-50"}`}>
                  <span className="font-medium">{k.name}</span> <span className="text-xs text-slate-500">{k.slug}</span> <Badge tone={k.visibility === "public" ? "green" : "amber"}>{k.visibility}</Badge>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      {kbId ? <PermissionEditor kbId={kbId} onSaved={() => void kbs.reload()} /> : <EmptyState title="选择知识库管理权限" hint="仅知识库所有者、KB 管理员或系统管理员可修改" />}
    </div>
  );
}

function CreateKb({ onCreated }: { onCreated: (id: string) => void }) {
  const { token } = useAuth();
  const [f, setF] = useState({ slug: "", name: "", description: "", visibility: "restricted" as "public" | "restricted" });
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const x: Record<string, string> = {};
    if (!SLUG.test(f.slug)) x.slug = "2-63 位小写字母、数字或连字符，且以字母或数字开头";
    if (!f.name.trim()) x.name = "请输入名称";
    setErrs(x);
    if (Object.keys(x).length) return;
    setError(null);
    try {
      const r = await platform.createKb(token, { ...f, name: f.name.trim() });
      setOk(`已创建 ${r.slug}`);
      setF({ slug: "", name: "", description: "", visibility: "restricted" });
      onCreated(r.id);
    } catch (err) {
      setError(errText(err));
    }
  }
  return (
    <Card title="新建知识库" label="create-kb">
      <form onSubmit={submit} noValidate className="space-y-3">
        <Field label="标识 (slug)" error={errs.slug} hint="例如 payment-runbooks">
          <input aria-label="标识" className={`${input} w-full`} value={f.slug} onChange={(e) => setF({ ...f, slug: e.target.value })} />
        </Field>
        <Field label="名称" error={errs.name}>
          <input aria-label="名称" className={`${input} w-full`} maxLength={200} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        </Field>
        <Field label="描述">
          <input aria-label="描述" className={`${input} w-full`} maxLength={2000} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
        </Field>
        <Field label="可见性">
          <select aria-label="可见性" className={`${select} w-full`} value={f.visibility} onChange={(e) => setF({ ...f, visibility: e.target.value as "public" | "restricted" })}>
            <option value="restricted">受限（仅所有者与授权用户）</option>
            <option value="public">公开（全部员工可读）</option>
          </select>
        </Field>
        <ErrorText error={error} />
        {ok && <p className="text-sm text-emerald-700">{ok}</p>}
        <button className={btnPrimary} type="submit">
          创建
        </button>
      </form>
    </Card>
  );
}

function PermissionEditor({ kbId, onSaved }: { kbId: string; onSaved: () => void }) {
  const { token } = useAuth();
  const perms = useAsync(() => platform.kbPermissions(token, kbId), [token, kbId]);
  const dir = useAsync(() => platform.directory(token), [token]);
  const [draft, setDraft] = useState<KbPermissions | null>(null);
  const [add, setAdd] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  useEffect(() => setDraft(perms.data), [perms.data]);

  if (perms.error) return <ErrorText error={perms.error} />;
  if (!draft) return <Loading />;
  const removed = (perms.data?.grants ?? []).filter((g) => !draft.grants.some((x) => x.user_id === g.user_id));

  async function save() {
    setConfirm(false);
    setError(null);
    try {
      const r = await platform.putKbPermissions(token, kbId, { visibility: draft!.visibility, grants: draft!.grants.map((g) => ({ user_id: g.user_id, permission: g.permission })) });
      perms.setData(r);
      setOk("权限已保存并写入审计日志");
      onSaved();
    } catch (e) {
      setError(errText(e));
    }
  }
  return (
    <Card title={`权限 · ${draft.slug}`} label="kb-permissions">
      <div className="space-y-3 text-sm">
        <p className="text-xs text-slate-500">所有者：{draft.owner.display_name}（拥有全部权限）</p>
        <Field label="可见性">
          <select aria-label="权限可见性" className={`${select} w-full`} value={draft.visibility} onChange={(e) => setDraft({ ...draft, visibility: e.target.value as "public" | "restricted" })}>
            <option value="restricted">受限</option>
            <option value="public">公开</option>
          </select>
        </Field>
        <table className="w-full text-sm" aria-label="授权列表">
          <thead className="text-left text-xs text-slate-500">
            <tr>
              <th>用户</th>
              <th>权限</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {draft.grants.map((g) => (
              <tr key={g.user_id} className="border-t">
                <td className="py-1">
                  {g.display_name} <span className="text-xs text-slate-500">{g.email}</span>
                </td>
                <td>
                  <select
                    aria-label={`${g.email} 权限`}
                    className={select}
                    value={g.permission}
                    onChange={(e) => setDraft({ ...draft, grants: draft.grants.map((x) => (x.user_id === g.user_id ? { ...x, permission: e.target.value as "read" } : x)) })}
                  >
                    <option value="read">读</option>
                    <option value="write">读写</option>
                    <option value="admin">管理</option>
                  </select>
                </td>
                <td className="text-right">
                  <button className={btn} onClick={() => setDraft({ ...draft, grants: draft.grants.filter((x) => x.user_id !== g.user_id) })}>
                    移除
                  </button>
                </td>
              </tr>
            ))}
            {!draft.grants.length && (
              <tr>
                <td colSpan={3} className="py-2 text-xs text-slate-400">
                  暂无单独授权
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="flex gap-2">
          <select aria-label="添加用户" className={`${select} flex-1`} value={add} onChange={(e) => setAdd(e.target.value)}>
            <option value="">选择用户…</option>
            {dir.data
              ?.filter((u) => u.id !== draft.owner.id && !draft.grants.some((g) => g.user_id === u.id))
              .map((u) => (
                <option key={u.id} value={u.id}>
                  {u.display_name} ({u.email})
                </option>
              ))}
          </select>
          <button
            className={btn}
            disabled={!add}
            onClick={() => {
              const u = dir.data!.find((x) => x.id === add)!;
              setDraft({ ...draft, grants: [...draft.grants, { user_id: u.id, email: u.email, display_name: u.display_name, permission: "read" }] });
              setAdd("");
            }}
          >
            添加
          </button>
        </div>
        {dir.error && <p className="text-xs text-slate-500">用户目录不可用（仅系统管理员/技术支持可读）</p>}
        <ErrorText error={error} />
        {ok && <p className="text-sm text-emerald-700">{ok}</p>}
        <button className={btnPrimary} onClick={() => (removed.length ? setConfirm(true) : void save())}>
          保存权限
        </button>
      </div>
      <ConfirmDialog
        open={confirm}
        title="撤销访问权限"
        body={`将撤销 ${removed.map((r) => r.display_name).join("、")} 对该知识库的访问。确认保存？`}
        confirmText="保存"
        onCancel={() => setConfirm(false)}
        onConfirm={() => void save()}
      />
    </Card>
  );
}
