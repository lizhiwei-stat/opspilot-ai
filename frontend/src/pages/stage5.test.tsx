import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { type Route, mockFetch } from "../test-utils";

const users = {
  admin: { id: "a", email: "admin@opspilot.local", display_name: "Ops Admin", role: "admin" },
  sam: { id: "s", email: "sam@opspilot.local", display_name: "Sam", role: "support" },
  bob: { id: "b", email: "bob@opspilot.local", display_name: "Bob", role: "user" },
};

function boot(path: string, who: keyof typeof users, route: Route) {
  sessionStorage.setItem("opspilot.token", "tok");
  window.history.replaceState(null, "", path);
  const calls: string[] = [];
  const f = mockFetch((url, init) => {
    calls.push(`${init?.method ?? "GET"} ${url} ${init?.body ?? ""}`);
    if (url.endsWith("/auth/me")) return { body: users[who] };
    if (url.endsWith("/system/version")) return { body: { version: "0.5.0", stage: 5, env: "test", llm_provider: "mock", embedding_provider: "mock" } };
    return route(url, init);
  });
  render(<App />);
  return { calls, f };
}

beforeEach(() => sessionStorage.clear());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const emptyDash = {
  scope: "own", generated_at: "2026-11-08T00:00:00Z",
  tickets: { total: 0, pending: 0, completed: 0, by_status: {}, pending_drafts: 0, source: "" },
  agent_runs: { total: 0, by_status: {}, task_success: { value: null, numerator: 0, denominator: 0 }, daily: [], latency: { p50_ms: null, p95_ms: null, n: 0, source: "" } },
  tool_success: { value: null, numerator: 0, denominator: 0, by_status: {} },
  usage: { mock: { runs: 0, tokens_in: 0, tokens_out: 0, cost_cny: 0 }, real: { runs: 0, tokens_in: 0, tokens_out: 0, cost_cny: 0 }, note: "", source: "" },
  recent_runs: [],
};

describe("Dashboard", () => {
  it("shows empty states and 'unavailable' instead of invented numbers", async () => {
    boot("/", "bob", (u) => (u.endsWith("/dashboard/summary") ? { body: emptyDash } : undefined));
    expect(await screen.findByText("还没有智能体运行记录")).toBeTruthy();
    expect(screen.getByLabelText("stat-任务成功率").textContent).toContain("不可用");
    expect(screen.getByLabelText("stat-运行延迟 P50 / P95").textContent).toContain("不可用");
    expect(screen.getByText("近 14 天无运行记录")).toBeTruthy();
  });
});

const ticket = {
  number: "TCK-0009", title: "订单超时", priority: "P2", status: "open", service: "order-service", category: "incident",
  source_draft_id: "d1", created_at: "2026-11-08T00:00:00Z", description: "desc", resolution: null,
  events: [{ from_status: null, to_status: "open", actor_id: "a", actor_name: "Ops Admin", approver_id: "a", approver_name: "Ops Admin", approval_id: "ap123456789", approved_at: null, note: "created", created_at: "2026-11-08T00:00:00Z" }],
};

describe("Tickets", () => {
  it("support can transition with validation; resolve requires a resolution", async () => {
    const { calls } = boot("/tickets/TCK-0009", "sam", (u, init) => {
      if (u.includes("/ticket-transitions")) return { body: { open: ["in_progress", "resolved", "closed"] } };
      if (u.includes("/tickets/TCK-0009") && init?.method === "PATCH") return { body: { ...ticket, status: "resolved" } };
      if (u.includes("/tickets/TCK-0009")) return { body: ticket };
      if (u.includes("/api/v1/tickets")) return { body: [ticket] };
      return undefined;
    });
    const form = await screen.findByRole("form", { name: "状态流转" });
    expect(screen.getByLabelText("ticket-events").textContent).toContain("审批 ap123456");
    fireEvent.click(within(form).getByRole("button", { name: "提交流转" }));
    expect(await screen.findByText("请选择目标状态")).toBeTruthy();
    fireEvent.change(within(form).getByLabelText("目标状态"), { target: { value: "resolved" } });
    fireEvent.change(within(form).getByLabelText("处理说明"), { target: { value: "done" } });
    fireEvent.click(within(form).getByRole("button", { name: "提交流转" }));
    expect(await screen.findByText("解决工单需要填写解决方案")).toBeTruthy();
    fireEvent.change(within(form).getByLabelText("解决方案"), { target: { value: "扩容" } });
    fireEvent.click(within(form).getByRole("button", { name: "提交流转" }));
    await waitFor(() => expect(calls.some((c) => c.startsWith("PATCH") && c.includes('"resolution":"扩容"'))).toBe(true));
  });

  it("ordinary users get no transition form", async () => {
    boot("/tickets/TCK-0009", "bob", (u) => {
      if (u.includes("/ticket-transitions")) return { body: { open: ["in_progress"] } };
      if (u.includes("/tickets/TCK-0009")) return { body: ticket };
      if (u.includes("/api/v1/tickets")) return { body: [ticket] };
      return undefined;
    });
    expect(await screen.findByText(/普通业务用户只能查看本人工单/)).toBeTruthy();
    expect(screen.queryByRole("form", { name: "状态流转" })).toBeNull();
  });

  it("admin approves a draft via the agent approval API; reject needs confirmation", async () => {
    const draft = { draft_id: "d1", run_id: "r1", action: "create", title: "订单超时", priority: "P2", service: "order-service", status: "pending_approval", revision: 1, parent_draft_id: null, created_by: "Alice", created_at: "2026-11-08T00:00:00Z", evidence_count: 2, approval: null };
    const { calls } = boot("/tickets?tab=drafts", "admin", (u) => {
      if (u.includes("/ticket-drafts")) return { body: [draft] };
      if (u.includes("/agent/runs/r1/approval")) return { body: { draft_result: { ticket_number: "TCK-0010", decision: "approved" } } };
      return undefined;
    });
    const item = await screen.findByLabelText("草稿 订单超时");
    fireEvent.click(within(item).getByRole("button", { name: "退回修改" }));
    expect(await within(item).findByText("退回修改必须填写意见")).toBeTruthy();
    fireEvent.click(within(item).getByRole("button", { name: "拒绝" }));
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(calls.some((c) => c.includes("/approval"))).toBe(false);
    fireEvent.click(within(item).getByRole("button", { name: "批准" }));
    expect((await screen.findByLabelText("审批结果")).textContent).toContain("已批准并创建工单 TCK-0010");
    expect(calls.find((c) => c.includes("/approval"))).toContain('"decision":"approved"');
  });
});

describe("Admin users", () => {
  const list = [
    { id: "a", email: "admin@opspilot.local", display_name: "Ops Admin", role: "admin", role_label: "系统管理员", is_active: true, last_login_at: null, created_at: "2026-11-08T00:00:00Z" },
  ];
  it("demoting asks for confirmation and surfaces the last-admin guard", async () => {
    boot("/admin/users", "admin", (u, init) => {
      if (u.includes("/admin/users/a") && init?.method === "PATCH")
        return { status: 409, body: { error: { code: "conflict", message: "cannot demote or deactivate the last active admin" } } };
      if (u.includes("/admin/users")) return { body: list };
      return undefined;
    });
    const sel = await screen.findByLabelText("admin@opspilot.local 角色");
    fireEvent.change(sel, { target: { value: "user" } });
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "确认" }));
    expect(await screen.findByText(/last active admin/)).toBeTruthy();
  });

  it("validates the create form client-side (password policy) before calling the API", async () => {
    const { calls } = boot("/admin/users", "admin", (u) => (u.includes("/admin/users") ? { body: list } : undefined));
    const form = await screen.findByLabelText("create-user");
    fireEvent.change(within(form).getByLabelText("新用户邮箱"), { target: { value: "bad" } });
    fireEvent.change(within(form).getByLabelText("初始密码"), { target: { value: "short" } });
    fireEvent.click(within(form).getByRole("button", { name: "创建用户" }));
    expect(await within(form).findByText("邮箱格式不正确")).toBeTruthy();
    expect(within(form).getByText("至少 10 位，且同时包含字母和数字")).toBeTruthy();
    expect(calls.some((c) => c.startsWith("POST") && c.includes("/admin/users"))).toBe(false);
  });
});

describe("Eval center", () => {
  it("keeps groups separate, marks mock rows and shows unavailable metrics", async () => {
    const row = (o: object) => ({ kind: "agent", file: "f.json", generated_at: "20261102T105133Z", dataset: "agent_eval_v1.jsonl", split: null, dataset_sha256: null, label_source: null, version: null, model: "mock-agent-v1", provider: "mock", is_mock: true, environment: "box", generator: null, n: 45, mode: "mcp-stdio", disclaimer: "MOCK 结果：xx", metrics: { task_success_rate: 1, recall_at_5: null, mrr_at_10: null }, ...o });
    boot("/evals", "bob", (u) =>
      u.includes("/evals/results")
        ? { body: { source: "evals/results", available: true, skipped_files: [], note: "", rows: [row({}), row({ provider: "qwen", model: "qwen-plus", is_mock: false, disclaimer: null })] } }
        : undefined,
    );
    const groups = await screen.findAllByLabelText("eval-group");
    expect(groups).toHaveLength(2);
    expect(within(groups[0]).getByText("MOCK 结果：xx")).toBeTruthy();
    expect(within(groups[0]).getAllByText("不可用").length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText("来源"), { target: { value: "real" } });
    await waitFor(() => expect(screen.getAllByLabelText("eval-group")).toHaveLength(1));
  });
});

describe("Workspace", () => {
  it("starts a streaming run, renders node events from SSE and the final answer", async () => {
    const run = { run_id: "r1", thread_id: "t12345678", turn: 1, status: "running", question: "ORD-5003?", answer: null, citations: [], node_trace: [], evidence: [], tools: [], is_mock: true, latency_ms: null, usage: { tokens_in: 0, tokens_out: 0, cost_cny: 0, source: "" }, llm: "mock", created_at: "2026-11-08T00:00:00Z", draft: null, refusal_reason: null, failure_reason: null };
    const done = { ...run, status: "completed", answer: "ORD-5003 是下游超时", node_trace: [{ turn: 1, node: "guard_in", status: "ok", ms: 1, detail: {} }], latency_ms: 30 };
    let streamed = false;
    const ev = (seq: number, type: string, data: object) => `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify({ seq, type, data, at: "t" })}\n\n`;
    const { calls } = boot("/workspace", "bob", (u) => {
      if (u.endsWith("/agent/threads")) return { body: [] };
      if (u.endsWith("/agent/runs")) return { body: run };
      if (u.includes("/events")) {
        streamed = true;
        return { raw: ev(1, "run_started", { kind: "turn" }) + ev(2, "node", { node: "guard_in", status: "ok", ms: 1 }) + ev(3, "final", { status: "completed" }) + 'event: end\ndata: {"status":"completed"}\n\n' };
      }
      if (u.includes("/agent/threads/t12345678")) return { body: [streamed ? done : run] };
      return undefined;
    });
    fireEvent.change(await screen.findByLabelText("输入问题"), { target: { value: "ORD-5003?" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("ORD-5003 是下游超时")).toBeTruthy();
    const list = screen.getByLabelText("事件列表");
    expect([...list.querySelectorAll("li")].map((li) => li.getAttribute("data-seq"))).toEqual(["1", "2", "3"]);
    expect(calls.find((c) => c.startsWith("POST") && c.includes("/agent/runs"))).toContain('"stream":true');
    expect(window.location.pathname).toBe("/workspace/t12345678");
    expect(screen.getByText(/不是逐 token 流式输出/)).toBeTruthy();
  });

  it("rejects an empty question client-side", async () => {
    boot("/workspace", "bob", (u) => (u.endsWith("/agent/threads") ? { body: [] } : undefined));
    fireEvent.click(await screen.findByRole("button", { name: "发送" }));
    expect(await screen.findByText("请输入问题")).toBeTruthy();
  });
});
