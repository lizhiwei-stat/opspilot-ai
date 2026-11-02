import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentRun, User } from "../api";
import { mockFetch } from "../test-utils";
import Agent from "./Agent";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const base: AgentRun = {
  run_id: "r1", thread_id: "t1234567890", turn: 1, status: "completed", question: "q", intent: "troubleshoot",
  answer: "【Mock】\n[1] 错误码 ORD-5003：所属服务 order-service", citations: [{ index: 1, source: "tool", title: "analyze_error_code", locator: "", id: "c1" }],
  node_trace: [
    { turn: 1, node: "guard_in", status: "ok", ms: 0.1, detail: {} },
    { turn: 1, node: "agent", status: "ok", ms: 1.2, detail: { accepted: ["analyze_error_code"] } },
    { turn: 1, node: "tools", status: "ok", ms: 3, detail: {} },
  ],
  evidence: [{ id: "c1", source: "tool", title: "analyze_error_code", locator: "", flagged: false, score: null, snippet: "错误码 ORD-5003" }],
  assessment: { decision: "sufficient", reasons: ["retrieval_gate_ok"], missing: [], contradictions: [] },
  refusal_reason: null, policy_violations: [], degraded: [],
  tools: [{ tool: "analyze_error_code", status: "ok", args: { code: "ORD-5003" }, result: "ORD-5003: service=order-service", error_class: null, attempt: 1, duration_ms: 3, idempotency_key: null }],
  pending_approval: null, draft: null,
  usage: { tokens_in: 42, tokens_out: 7, cost_cny: 0, source: "estimate (mock: chars, no API)" },
  latency_ms: 88, failure_reason: null, is_mock: true, llm: "mock:mock-agent-v1", created_at: "2026-11-02T00:00:00Z",
};
const admin: User = { id: "a", email: "admin@opspilot.local", display_name: "Admin", role: "admin" };

describe("Agent workbench", () => {
  it("runs via the API and shows trace, tools, evidence, tokens, latency and the mock label", async () => {
    const calls: string[] = [];
    mockFetch((url, init) => {
      calls.push(`${init?.method ?? "GET"} ${url} ${init?.body ?? ""}`);
      if (url.endsWith("/api/v1/agent/runs")) return { body: base };
      return undefined;
    });
    render(<Agent token="tok" user={admin} />);
    fireEvent.click(screen.getByRole("button", { name: "运行" }));
    await waitFor(() => expect(screen.getByText("已完成")).toBeTruthy());
    expect(calls[0]).toContain("POST /api/v1/agent/runs");
    expect(screen.getByText(/Mock 智能体：/)).toBeTruthy();
    expect(screen.getByText("1. guard_in")).toBeTruthy();
    expect(screen.getByText("3. tools")).toBeTruthy();
    expect(screen.getByText(/参数：\{"code":"ORD-5003"\}/)).toBeTruthy();
    expect(screen.getByLabelText("tokens-in").textContent).toBe("42");
    expect(screen.getByLabelText("latency").textContent).toContain("88");
    expect(screen.getByText(/不展示模型内部推理过程/)).toBeTruthy();
    // follow-up keeps the thread
    fireEvent.click(screen.getByRole("button", { name: "继续追问" }));
    await waitFor(() => expect(calls.length).toBe(2));
    expect(calls[1]).toContain('"thread_id":"t1234567890"');
  });

  it("shows the approval card for admins and resumes with a decision", async () => {
    const pending: AgentRun = {
      ...base, status: "awaiting_approval", answer: "已生成待审批工单草稿",
      pending_approval: { type: "ticket_draft_approval", draft_id: "d1", payload_sha256: "x" },
      draft: { draft_id: "d1", status: "pending_approval", title: "订单接口 500", priority: "P2", service: "order-service" },
    };
    mockFetch((url) => {
      if (url.endsWith("/approval")) return { body: { ...pending, status: "completed", pending_approval: null, draft: { ...pending.draft!, status: "approved" } } };
      if (url.endsWith("/api/v1/agent/runs")) return { body: pending };
      return undefined;
    });
    render(<Agent token="tok" user={admin} />);
    fireEvent.click(screen.getByRole("button", { name: "运行" }));
    await waitFor(() => expect(screen.getByText("等待审批")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    await waitFor(() => expect(screen.getByLabelText("draft-status").textContent).toContain("approved"));
  });

  it("shows refusal reason and missing info", async () => {
    mockFetch((url) =>
      url.endsWith("/api/v1/agent/runs")
        ? { body: { ...base, status: "refused", answer: "无法给出可靠回答", citations: [], refusal_reason: "head_term_absent_from_accessible_kb", assessment: { decision: "refuse", missing: ["没有关于「年假」的信息"] } } }
        : undefined,
    );
    render(<Agent token="tok" user={null} />);
    fireEvent.click(screen.getByRole("button", { name: "运行" }));
    await waitFor(() => expect(screen.getByText("已拒答")).toBeTruthy());
    expect(screen.getByText(/head_term_absent_from_accessible_kb/)).toBeTruthy();
    expect(screen.getByText(/年假/)).toBeTruthy();
  });

  it("Stage 4: queue, return-for-revision (comment required), approve -> ticket + events, MCP audit", async () => {
    const calls: string[] = [];
    const draft = { draft_id: "d1", status: "pending_approval", title: "[order-service] ORD-5003", priority: "P2", service: "order-service", description: "解决方案…", revision: 1, parent_draft_id: null };
    const pending: AgentRun = {
      ...base, intent: "incident_ticket", status: "awaiting_approval", answer: "已生成待审核工单草稿",
      pending_approval: { type: "ticket_draft_approval", draft_id: "d1", payload_sha256: "x" }, draft,
    };
    const revised: AgentRun = { ...pending, draft: { ...draft, draft_id: "d2", revision: 2, parent_draft_id: "d1" } };
    const done: AgentRun = {
      ...revised, status: "completed", pending_approval: null, draft: { ...revised.draft!, status: "executed" },
      draft_result: { status: "executed", decision: "approved", approval_id: "ap1", reviewer_id: "a", decided_at: "2026-11-02T10:00:00Z", revision: 2, ticket_number: "TCK-0009", business_op: "update_ticket_status" },
    };
    let step = 0;
    mockFetch((url, init) => {
      calls.push(`${init?.method ?? "GET"} ${url} ${init?.body ?? ""}`);
      if (url.endsWith("/api/v1/agent/approvals")) return { body: [pending] };
      if (url.endsWith("/approval")) return { body: step++ === 0 ? revised : done };
      if (url.includes("/api/v1/tickets/TCK-0009"))
        return { body: { number: "TCK-0009", title: "t", priority: "P2", status: "open", service: "order-service", category: "incident", source_draft_id: "d2", created_at: "x", events: [{ from_status: null, to_status: "open", actor_id: "a", approver_id: "admin-uuid", approval_id: "ap1", approved_at: "2026-11-02T10:00:00Z", note: null, created_at: "x" }] } };
      if (url.includes("/api/v1/mcp/audit"))
        return { body: [{ side: "server", server: "ticket", tool: "update_ticket_status", transport: "stdio", user_role: "admin", thread_id: "t1", args_summary: {}, args_sha256: "h", status: "ok", error_class: null, error: null, duration_ms: 12, output_bytes: 100, created_at: "x" }] };
      return undefined;
    });
    render(<Agent token="tok" user={admin} />);
    fireEvent.click(screen.getByRole("button", { name: "刷新队列" }));
    fireEvent.click(await screen.findByRole("button", { name: /打开 r1/ }));
    expect(screen.getByLabelText("草稿内容").textContent).toContain("解决方案");
    const back = screen.getByRole("button", { name: "退回修改" }) as HTMLButtonElement;
    expect(back.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("审批意见"), { target: { value: "补充影响范围" } });
    fireEvent.click(back);
    await waitFor(() => expect(screen.getByText(/第 2 版/)).toBeTruthy());
    expect(calls.some((c) => c.includes('"decision":"returned"') && c.includes("补充影响范围"))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    await waitFor(() => expect(screen.getByLabelText("approval-result").textContent).toContain("TCK-0009"));
    fireEvent.click(screen.getByRole("button", { name: "查看工单与状态流转" }));
    await waitFor(() => expect(screen.getByLabelText("ticket-view").textContent).toContain("→ open"));
    fireEvent.click(screen.getByRole("button", { name: "加载审计" }));
    await waitFor(() => expect(screen.getAllByText("update_ticket_status").length).toBeGreaterThan(0));
  });

  it("non-admins see no approval buttons or queue", async () => {
    const pending: AgentRun = {
      ...base, status: "awaiting_approval",
      pending_approval: { type: "ticket_draft_approval", draft_id: "d1", payload_sha256: "x" },
      draft: { draft_id: "d1", status: "pending_approval", title: "t", priority: "P2", service: null },
    };
    mockFetch((url) => (url.endsWith("/api/v1/agent/runs") ? { body: pending } : undefined));
    render(<Agent token="tok" user={{ ...admin, role: "user" }} />);
    fireEvent.click(screen.getByRole("button", { name: "运行" }));
    await waitFor(() => expect(screen.getByText("等待审批")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "批准" })).toBeNull();
    expect(screen.queryByRole("button", { name: "刷新队列" })).toBeNull();
  });
});
