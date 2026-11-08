import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { mockFetch } from "./test-utils";

const admin = { id: "a", email: "admin@opspilot.local", display_name: "Ops Admin", role: "admin" };
const bob = { id: "b", email: "bob@opspilot.local", display_name: "Bob", role: "user" };
const dash = {
  scope: "all",
  generated_at: "2026-11-08T00:00:00Z",
  tickets: { total: 4, pending: 3, completed: 1, by_status: { open: 2, in_progress: 1, resolved: 1 }, pending_drafts: 1, source: "tickets" },
  agent_runs: {
    total: 5,
    by_status: { completed: 3, refused: 1, awaiting_approval: 1 },
    task_success: { value: 0.75, numerator: 3, denominator: 4 },
    daily: [{ day: "2026-11-08", runs: 5, completed: 3 }],
    latency: { p50_ms: 40, p95_ms: 120, n: 5, source: "agent_runs" },
  },
  tool_success: { value: 0.5, numerator: 1, denominator: 2, by_status: { ok: 1, error: 1 } },
  usage: { mock: { runs: 5, tokens_in: 10, tokens_out: 20, cost_cny: 0 }, real: { runs: 0, tokens_in: 0, tokens_out: 0, cost_cny: 0 }, note: "", source: "" },
  recent_runs: [],
};

function go(path: string) {
  window.history.replaceState(null, "", path);
}

beforeEach(() => {
  sessionStorage.clear();
  go("/");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
});

const version = { name: "OpsPilot AI", version: "0.5.0", stage: 5, env: "test", llm_provider: "mock", llm_model: "m", embedding_provider: "mock", embedding_model: "m", embedding_dim: 8 };

describe("App shell", () => {
  it("redirects to /login and validates the form before calling the API", async () => {
    const f = mockFetch(() => undefined);
    render(<App />);
    await screen.findByRole("form", { name: "登录表单" });
    expect(window.location.pathname).toBe("/login");
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByText("请输入有效邮箱")).toBeTruthy();
    expect(screen.getByText("请输入密码")).toBeTruthy();
    expect(f.mock.calls.filter(([u]) => String(u).includes("/auth/login"))).toHaveLength(0);
  });

  it("logs in, stores only the token, shows the real dashboard numbers with denominators", async () => {
    mockFetch((url) => {
      if (url.endsWith("/auth/login")) return { body: { access_token: "tok", expires_in: 3600, user: admin } };
      if (url.endsWith("/auth/me")) return { body: admin };
      if (url.endsWith("/system/version")) return { body: version };
      if (url.endsWith("/dashboard/summary")) return { body: dash };
      return undefined;
    });
    go("/login?next=%2F");
    render(<App />);
    fireEvent.change(await screen.findByLabelText("邮箱"), { target: { value: "admin@opspilot.local" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByText("3 / 4 · completed / 已结束运行")).toBeTruthy();
    expect(screen.getByLabelText("stat-任务成功率").textContent).toContain("75.0%");
    expect(screen.getByLabelText("stat-运行延迟 P50 / P95").textContent).toContain("40 / 120 ms");
    expect(sessionStorage.getItem("opspilot.token")).toBe("tok");
    expect(Object.keys(sessionStorage)).toEqual(["opspilot.token"]);
    expect(screen.getByRole("link", { name: /用户与权限/ })).toBeTruthy();
    expect(screen.getByText("系统管理员")).toBeTruthy();
  });

  it("shows a friendly message on a wrong password (401 from login does not loop)", async () => {
    mockFetch((url) => (url.endsWith("/auth/login") ? { status: 401, body: { error: { code: "unauthorized", message: "bad" } } } : undefined));
    go("/login");
    render(<App />);
    fireEvent.change(await screen.findByLabelText("邮箱"), { target: { value: "x@y.io" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByText("邮箱或密码错误")).toBeTruthy();
  });

  it("hides admin navigation from ordinary users and blocks the page", async () => {
    sessionStorage.setItem("opspilot.token", "tok");
    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return { body: bob };
      if (url.endsWith("/system/version")) return { body: version };
      return undefined;
    });
    go("/admin/users");
    render(<App />);
    expect(await screen.findByLabelText("forbidden")).toBeTruthy();
    expect(screen.queryByRole("link", { name: /用户与权限/ })).toBeNull();
    expect(screen.getByText("普通业务用户")).toBeTruthy();
  });

  it("signs out on a 401 from any authenticated call and explains why", async () => {
    sessionStorage.setItem("opspilot.token", "tok");
    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return { body: admin };
      if (url.endsWith("/dashboard/summary")) return { status: 401, body: { error: { code: "unauthorized", message: "expired" } } };
      return undefined;
    });
    render(<App />);
    expect(await screen.findByText("会话已失效，请重新登录")).toBeTruthy();
    expect(sessionStorage.getItem("opspilot.token")).toBeNull();
    await waitFor(() => expect(window.location.pathname).toBe("/login"));
  });

  it("maps Stage 4 hash bookmarks to the new routes", async () => {
    sessionStorage.setItem("opspilot.token", "tok");
    mockFetch((url) => (url.endsWith("/auth/me") ? { body: bob } : url.includes("/knowledge-bases") ? { body: [] } : undefined));
    go("/#qa");
    await act(async () => {
      render(<App />);
    });
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe("/knowledge?tab=qa"));
  });
});
