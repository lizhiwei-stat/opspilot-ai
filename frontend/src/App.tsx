import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { ApiError, api, type Role, ROLE_LABEL, type Version } from "./api";
import { Badge, ErrorText, Field, Loading, btn, btnPrimary, input } from "./components/ui";
import { Link, match, navigate, query, usePath } from "./lib/router";
import { SessionProvider, useSession } from "./lib/session";
import Agent from "./pages/Agent";
import AdminUsers from "./pages/AdminUsers";
import Dashboard from "./pages/Dashboard";
import EvalCenter from "./pages/EvalCenter";
import Executions from "./pages/Executions";
import KnowledgeCenter from "./pages/KnowledgeCenter";
import Settings from "./pages/Settings";
import Tickets from "./pages/Tickets";
import Workspace from "./pages/Workspace";

interface NavItem {
  to: string;
  label: string;
  icon: string;
  roles?: Role[];
}
export const NAV: NavItem[] = [
  { to: "/", label: "仪表盘", icon: "▦" },
  { to: "/workspace", label: "智能体工作台", icon: "✦" },
  { to: "/knowledge", label: "知识库", icon: "▤" },
  { to: "/tickets", label: "工单中心", icon: "☰" },
  { to: "/executions", label: "执行可视化", icon: "⇢" },
  { to: "/evals", label: "评测与监控", icon: "◔" },
  { to: "/admin/users", label: "用户与权限", icon: "⚑", roles: ["admin"] },
  { to: "/admin/settings", label: "系统设置", icon: "⚙", roles: ["admin"] },
  { to: "/lab/agent", label: "Agent 调试台", icon: "⌁" },
];

// Stage <=4 used hash tabs; keep old bookmarks working.
const LEGACY: Record<string, string> = {
  "#overview": "/",
  "#knowledge": "/knowledge",
  "#search": "/knowledge?tab=search",
  "#qa": "/knowledge?tab=qa",
  "#agent": "/workspace",
  "#evals": "/evals",
};

export default function App() {
  return (
    <SessionProvider>
      <Shell />
    </SessionProvider>
  );
}

function Shell() {
  const path = usePath();
  const s = useSession();
  const pathname = path.split("?")[0];

  useEffect(() => {
    const target = LEGACY[window.location.hash];
    if (target) navigate(target, true);
  }, []);

  useEffect(() => {
    if (s.loading) return;
    if (!s.token && pathname !== "/login") navigate(`/login?next=${encodeURIComponent(path)}`, true);
    if (s.token && pathname === "/login") navigate(query(path).get("next") || "/", true);
  }, [s.loading, s.token, pathname, path]);

  if (s.loading) return <Loading label="正在恢复会话…" />;
  if (pathname === "/login" || !s.token || !s.user) return <LoginPage />;

  const item = NAV.find((n) => (n.to === "/" ? pathname === "/" : pathname === n.to || pathname.startsWith(`${n.to}/`)));
  const allowed = !item?.roles || s.can(...item.roles);
  return (
    <Layout pathname={pathname}>
      {!allowed ? (
        <Forbidden />
      ) : (
        route(pathname, path)
      )}
    </Layout>
  );
}

function route(pathname: string, path: string): ReactNode {
  let m: Record<string, string> | null;
  if (pathname === "/") return <Dashboard />;
  if (pathname === "/workspace") return <Workspace />;
  if ((m = match("/workspace/:threadId", pathname))) return <Workspace threadId={m.threadId} />;
  if (pathname === "/knowledge") return <KnowledgeCenter tab={query(path).get("tab") ?? "docs"} />;
  if (pathname === "/tickets") return <Tickets tab={query(path).get("tab") ?? "list"} />;
  if ((m = match("/tickets/:number", pathname))) return <Tickets tab="list" number={m.number} />;
  if (pathname === "/executions") return <Executions />;
  if ((m = match("/executions/:runId", pathname))) return <Executions runId={m.runId} />;
  if (pathname === "/evals") return <EvalCenter tab={query(path).get("tab") ?? "results"} />;
  if (pathname === "/admin/users") return <AdminUsers />;
  if (pathname === "/admin/settings") return <Settings />;
  if (pathname === "/lab/agent") return <LegacyAgent />;
  return (
    <div className="rounded-xl border bg-white p-8 text-center">
      <p className="font-medium">页面不存在</p>
      <Link to="/" className="mt-2 inline-block text-sm text-indigo-600 underline">
        返回仪表盘
      </Link>
    </div>
  );
}

function LegacyAgent() {
  const { token, user } = useSession();
  return <Agent token={token!} user={user} />;
}

function Forbidden() {
  return (
    <div role="alert" aria-label="forbidden" className="rounded-xl border border-rose-200 bg-rose-50 p-8 text-center text-rose-700">
      <p className="font-medium">无权访问此页面</p>
      <p className="mt-1 text-sm">该页面需要更高权限；后端接口同样会拒绝（HTTP 403）。</p>
    </div>
  );
}

function Layout({ pathname, children }: { pathname: string; children: ReactNode }) {
  const s = useSession();
  const [open, setOpen] = useState(false);
  const [version, setVersion] = useState<Version | null>(null);
  useEffect(() => {
    api.version().then(setVersion).catch(() => setVersion(null));
  }, []);
  const items = NAV.filter((n) => !n.roles || s.can(...n.roles));
  const active = (to: string) => (to === "/" ? pathname === "/" : pathname === to || pathname.startsWith(`${to}/`));
  return (
    <div className="min-h-screen bg-slate-50 text-slate-800 lg:flex">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:p-2">
        跳到主要内容
      </a>
      <aside
        className={`${open ? "block" : "hidden"} border-r border-slate-200 bg-slate-900 text-slate-100 lg:sticky lg:top-0 lg:block lg:h-screen lg:w-60 lg:shrink-0`}
      >
        <div className="px-5 py-5">
          <p className="text-base font-semibold">OpsPilot AI</p>
          <p className="text-xs text-slate-400">企业智能运维支持平台</p>
        </div>
        <nav aria-label="主导航" className="space-y-0.5 px-3">
          {items.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              onClick={() => setOpen(false)}
              aria-current={active(n.to) ? "page" : undefined}
              className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm ${active(n.to) ? "bg-indigo-600 text-white" : "text-slate-300 hover:bg-slate-800 hover:text-white"}`}
            >
              <span aria-hidden className="w-4 text-center">
                {n.icon}
              </span>
              {n.label}
            </Link>
          ))}
        </nav>
        {version && (
          <p className="px-5 py-4 text-[11px] text-slate-500">
            v{version.version} · Stage {version.stage} · {version.env}
            <br />
            LLM {version.llm_provider} · Emb {version.embedding_provider}
          </p>
        )}
      </aside>
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur lg:px-8">
          <button className={`${btn} lg:hidden`} aria-label="切换导航" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            ☰
          </button>
          <span className="hidden text-sm text-slate-500 lg:block">{NAV.find((n) => active(n.to))?.label}</span>
          <div className="flex items-center gap-3 text-sm" aria-label="当前用户">
            <span className="font-medium">{s.user?.display_name}</span>
            <Badge tone={s.user?.role === "admin" ? "indigo" : s.user?.role === "support" ? "sky" : "slate"}>{s.user ? ROLE_LABEL[s.user.role] : ""}</Badge>
            <button
              className={btn}
              onClick={() => {
                s.logout();
                navigate("/login", true);
              }}
            >
              退出登录
            </button>
          </div>
        </header>
        <main id="main" className="mx-auto max-w-7xl px-4 py-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function LoginPage() {
  const s = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const emailErr = touched && !EMAIL.test(email) ? "请输入有效邮箱" : null;
  const pwErr = touched && !password ? "请输入密码" : null;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (!EMAIL.test(email) || !password) return;
    setBusy(true);
    setError(null);
    try {
      await s.login(email.trim(), password);
      setPassword("");
    } catch (err) {
      setError(err instanceof ApiError ? (err.status === 401 ? "邮箱或密码错误" : `${err.message}（${err.code}）`) : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-900 to-indigo-900 p-4">
      <form onSubmit={onSubmit} noValidate aria-label="登录表单" className="w-full max-w-sm space-y-4 rounded-2xl bg-white p-7 shadow-2xl">
        <div>
          <h1 className="text-lg font-semibold">OpsPilot AI</h1>
          <p className="text-sm text-slate-500">企业智能运维支持平台 · 请登录</p>
        </div>
        {s.expired && <ErrorText error="会话已失效，请重新登录" />}
        <Field label="邮箱" error={emailErr}>
          <input className={`${input} w-full`} type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" aria-invalid={!!emailErr} />
        </Field>
        <Field label="密码" error={pwErr}>
          <input className={`${input} w-full`} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" aria-invalid={!!pwErr} />
        </Field>
        <ErrorText error={error} />
        <button type="submit" disabled={busy} className={`${btnPrimary} w-full py-2`}>
          {busy ? "登录中…" : "登录"}
        </button>
        <p className="text-xs text-slate-400">令牌仅保存在当前标签页 sessionStorage，关闭标签页即清除。</p>
      </form>
    </div>
  );
}
