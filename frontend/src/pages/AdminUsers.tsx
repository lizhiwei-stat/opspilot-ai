import { type FormEvent, useState } from "react";
import { type AdminUser, type Role, ROLE_LABEL, platform } from "../api";
import { Badge, Card, ConfirmDialog, ErrorText, Field, Loading, PageHeader, Tabs, btn, btnPrimary, input, select, when } from "../components/ui";
import { errText } from "../errors";
import { useAsync } from "../lib/hooks";
import { useAuth } from "../lib/session";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PW = /^(?=.*[A-Za-z])(?=.*\d).{10,128}$/;

export default function AdminUsers() {
  const [tab, setTab] = useState<"users" | "roles" | "audit">("users");
  return (
    <div>
      <PageHeader title="用户与权限" subtitle="三种角色：系统管理员 / 技术支持工程师 / 普通业务用户；最后一个启用的管理员不可降级或停用" />
      <Tabs
        label="管理视图"
        value={tab}
        onChange={setTab}
        items={[
          ["users", "用户"],
          ["roles", "角色与权限"],
          ["audit", "操作审计"],
        ]}
      />
      {tab === "users" && <Users />}
      {tab === "roles" && <Roles />}
      {tab === "audit" && <Audit />}
    </div>
  );
}

function Users() {
  const { token, user: me } = useAuth();
  const users = useAsync(() => platform.users(token), [token]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<{ u: AdminUser; patch: Partial<{ role: Role; is_active: boolean }>; text: string } | null>(null);

  async function apply(u: AdminUser, patch: Partial<{ role: Role; is_active: boolean; password: string }>) {
    setPending(null);
    setError(null);
    try {
      await platform.updateUser(token, u.id, patch);
      await users.reload();
    } catch (e) {
      setError(errText(e));
    }
  }
  function changeRole(u: AdminUser, role: Role) {
    if (u.role === "admin" && role !== "admin") setPending({ u, patch: { role }, text: `将 ${u.display_name} 从系统管理员降级为${ROLE_LABEL[role]}？` });
    else void apply(u, { role });
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_22rem]">
      <Card title="用户" label="user-table">
        <ErrorText error={error ?? users.error} />
        {users.loading && !users.data && <Loading />}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="py-1">用户</th>
                <th>角色</th>
                <th>状态</th>
                <th>最近登录</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.data?.map((u) => (
                <tr key={u.id} className="border-t" aria-label={`user-${u.email}`}>
                  <td className="py-1.5">
                    {u.display_name} {u.id === me.id && <Badge tone="indigo">我</Badge>}
                    <div className="text-xs text-slate-500">{u.email}</div>
                  </td>
                  <td>
                    <select aria-label={`${u.email} 角色`} className={select} value={u.role} onChange={(e) => changeRole(u, e.target.value as Role)}>
                      {(["admin", "support", "user"] as Role[]).map((r) => (
                        <option key={r} value={r}>
                          {ROLE_LABEL[r]}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>{u.is_active ? <Badge tone="green">启用</Badge> : <Badge tone="red">停用</Badge>}</td>
                  <td className="text-xs text-slate-500">{when(u.last_login_at)}</td>
                  <td className="text-right">
                    <button
                      className={btn}
                      onClick={() =>
                        u.is_active ? setPending({ u, patch: { is_active: false }, text: `停用 ${u.display_name}？停用后无法登录，已签发的令牌也会失效。` }) : void apply(u, { is_active: true })
                      }
                    >
                      {u.is_active ? "停用" : "启用"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <CreateUser onCreated={() => void users.reload()} />
      <ConfirmDialog open={!!pending} title="确认变更" body={pending?.text} onCancel={() => setPending(null)} onConfirm={() => pending && void apply(pending.u, pending.patch)} />
    </div>
  );
}

function CreateUser({ onCreated }: { onCreated: () => void }) {
  const { token } = useAuth();
  const blank = { email: "", display_name: "", role: "user" as Role, password: "" };
  const [f, setF] = useState(blank);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const x: Record<string, string> = {};
    if (!EMAIL.test(f.email)) x.email = "邮箱格式不正确";
    if (!f.display_name.trim()) x.display_name = "请输入显示名";
    if (!PW.test(f.password)) x.password = "至少 10 位，且同时包含字母和数字";
    setErrs(x);
    if (Object.keys(x).length) return;
    setError(null);
    try {
      const u = await platform.createUser(token, { ...f, email: f.email.trim(), display_name: f.display_name.trim() });
      setOk(`已创建 ${u.email}`);
      setF(blank);
      onCreated();
    } catch (err) {
      setError(errText(err));
    }
  }
  return (
    <Card title="新建用户" label="create-user">
      <form onSubmit={submit} noValidate className="space-y-3">
        <Field label="邮箱" error={errs.email}>
          <input aria-label="新用户邮箱" className={`${input} w-full`} value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
        </Field>
        <Field label="显示名" error={errs.display_name}>
          <input aria-label="显示名" className={`${input} w-full`} maxLength={100} value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
        </Field>
        <Field label="角色">
          <select aria-label="新用户角色" className={`${select} w-full`} value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as Role })}>
            {(["user", "support", "admin"] as Role[]).map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="初始密码" error={errs.password} hint="仅提交给后端哈希保存，前端不保留">
          <input aria-label="初始密码" type="password" autoComplete="new-password" className={`${input} w-full`} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
        </Field>
        <ErrorText error={error} />
        {ok && <p className="text-sm text-emerald-700">{ok}</p>}
        <button className={btnPrimary} type="submit">
          创建用户
        </button>
      </form>
    </Card>
  );
}

function Roles() {
  const { token } = useAuth();
  const roles = useAsync(() => platform.roles(token), [token]);
  return (
    <div className="grid gap-4 md:grid-cols-3">
      <ErrorText error={roles.error} />
      {roles.data?.map((r) => (
        <Card key={r.role} title={r.label} label={`role-${r.role}`}>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {r.capabilities.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
          <p className="mt-3 font-mono text-xs text-slate-400">{r.role}</p>
        </Card>
      ))}
    </div>
  );
}

function Audit() {
  const { token } = useAuth();
  const a = useAsync(() => platform.audit(token), [token]);
  return (
    <Card title="管理操作审计" label="admin-audit">
      <ErrorText error={a.error} />
      {a.loading && !a.data && <Loading />}
      <table className="w-full text-xs">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="py-1">时间</th>
            <th>操作人</th>
            <th>动作</th>
            <th>对象</th>
            <th>变更</th>
          </tr>
        </thead>
        <tbody>
          {a.data?.map((r) => (
            <tr key={r.id} className="border-t align-top">
              <td className="whitespace-nowrap py-1">{when(r.created_at)}</td>
              <td>{r.actor}</td>
              <td className="font-mono">{r.action}</td>
              <td className="font-mono">
                {r.target_type}:{r.target_id.slice(0, 8)}
              </td>
              <td className="max-w-md break-all text-slate-600">{JSON.stringify(r.detail)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
