import { type FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError, api, type Readiness, type User, type Version } from "./api";

const TOKEN_KEY = "opspilot.token";

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${
        ok ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"
      }`}
    >
      {label}
    </span>
  );
}

export default function App() {
  const [ready, setReady] = useState<Readiness | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [email, setEmail] = useState("alice@opspilot.local");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoadError(null);
    try {
      const [r, v] = await Promise.all([api.ready(), api.version()]);
      setReady(r);
      setVersion(v);
    } catch (e) {
      setLoadError(e instanceof ApiError ? `${e.code}: ${e.message}` : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const token = sessionStorage.getItem(TOKEN_KEY);
    if (token) api.me(token).then(setUser).catch(() => sessionStorage.removeItem(TOKEN_KEY));
  }, [refresh]);

  async function onLogin(e: FormEvent) {
    e.preventDefault();
    setAuthError(null);
    try {
      const res = await api.login(email, password);
      sessionStorage.setItem(TOKEN_KEY, res.access_token);
      setUser(await api.me(res.access_token));
      setPassword("");
    } catch (err) {
      setAuthError(err instanceof ApiError ? err.message : String(err));
    }
  }

  function logout() {
    sessionStorage.removeItem(TOKEN_KEY);
    setUser(null);
  }

  return (
    <div className="min-h-screen text-slate-800">
      <header className="border-b bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <h1 className="text-lg font-semibold">
            OpsPilot AI <span className="text-sm font-normal text-slate-500">智能技术支持平台</span>
          </h1>
          {version && (
            <span className="text-xs text-slate-500">
              v{version.version} · Stage {version.stage} · {version.env}
            </span>
          )}
        </div>
      </header>

      <main className="mx-auto grid max-w-5xl gap-6 px-6 py-8 md:grid-cols-2">
        <section className="rounded-xl border bg-white p-5 shadow-sm" aria-label="system-status">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold">系统状态</h2>
            <button onClick={() => void refresh()} className="text-sm text-indigo-600 hover:underline">
              刷新
            </button>
          </div>
          {loadError && <p className="text-sm text-rose-600">加载失败：{loadError}</p>}
          {ready && (
            <>
              <p className="mb-3">
                <Badge ok={ready.status === "ready"} label={ready.status === "ready" ? "就绪" : "未就绪"} />
              </p>
              <ul className="space-y-2">
                {ready.checks.map((c) => (
                  <li key={c.name} className="flex items-start justify-between gap-3 text-sm">
                    <span className="font-mono">{c.name}</span>
                    <span className="flex-1 truncate text-slate-500" title={c.detail}>
                      {c.detail}
                    </span>
                    <Badge ok={c.ok} label={c.ok ? `${c.latency_ms}ms` : "失败"} />
                  </li>
                ))}
              </ul>
            </>
          )}
          {version && (
            <dl className="mt-4 grid grid-cols-2 gap-1 border-t pt-3 text-xs text-slate-500">
              <dt>LLM</dt>
              <dd>
                {version.llm_provider} / {version.llm_model}
              </dd>
              <dt>Embedding</dt>
              <dd>
                {version.embedding_provider} / {version.embedding_model} ({version.embedding_dim})
              </dd>
            </dl>
          )}
        </section>

        <section className="rounded-xl border bg-white p-5 shadow-sm" aria-label="auth">
          <h2 className="mb-4 font-semibold">登录</h2>
          {user ? (
            <div className="space-y-2 text-sm">
              <p>
                已登录：<strong>{user.display_name}</strong>（{user.email}）
              </p>
              <p>
                角色：<Badge ok label={user.role === "admin" ? "管理员" : "普通用户"} />
              </p>
              <button onClick={logout} className="rounded-md border px-3 py-1.5 hover:bg-slate-50">
                退出登录
              </button>
            </div>
          ) : (
            <form onSubmit={onLogin} className="space-y-3 text-sm">
              <label className="block">
                邮箱
                <input
                  className="mt-1 w-full rounded-md border px-3 py-2"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="username"
                />
              </label>
              <label className="block">
                密码
                <input
                  type="password"
                  className="mt-1 w-full rounded-md border px-3 py-2"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                />
              </label>
              {authError && <p className="text-rose-600">{authError}</p>}
              <button type="submit" className="w-full rounded-md bg-indigo-600 py-2 text-white hover:bg-indigo-700">
                登录
              </button>
            </form>
          )}
        </section>
      </main>
    </div>
  );
}
