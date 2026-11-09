// Thin typed client for the real backend API (no mocks).
export interface Check {
  name: string;
  ok: boolean;
  latency_ms: number;
  detail: string;
}
export interface Readiness {
  status: "ready" | "not_ready";
  checks: Check[];
}
export interface Version {
  name: string;
  version: string;
  stage: number;
  env: string;
  llm_provider: string;
  llm_model: string;
  embedding_provider: string;
  embedding_model: string;
  embedding_dim: number;
}
export interface User {
  id: string;
  email: string;
  display_name: string;
  role: Role;
}
export type Role = "user" | "support" | "admin";
export const ROLE_LABEL: Record<Role, string> = { admin: "系统管理员", support: "技术支持工程师", user: "普通业务用户" };
export interface LoginResult {
  access_token: string;
  expires_in: number;
  user: User;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId: string | null,
  ) {
    super(message);
  }
}

export const BASE = import.meta.env.VITE_API_BASE ?? "";
/** Fired on any 401 from an authenticated call so the session layer can sign out once. */
export const UNAUTHORIZED_EVENT = "opspilot:unauthorized";

export async function request<T>(path: string, init?: RequestInit, okStatuses = [200]): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ApiError(0, "network_error", "无法连接后端服务", null);
  }
  const body = await res.json().catch(() => null);
  if (!okStatuses.includes(res.status)) {
    const e = body?.error;
    const hasAuth = !!init?.headers && "Authorization" in (init.headers as Record<string, string>);
    if (res.status === 401 && hasAuth) window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
    throw new ApiError(res.status, e?.code ?? "http_error", e?.message ?? res.statusText, e?.request_id ?? null);
  }
  return body as T;
}

export const api = {
  live: () => request<{ status: string; version: string }>("/health/live"),
  // 503 still carries a full report, so accept it and render failing checks.
  ready: () => request<Readiness>("/health/ready", undefined, [200, 503]),
  version: () => request<Version>("/api/v1/system/version"),
  login: (email: string, password: string) =>
    request<LoginResult>("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    }),
  me: (token: string) =>
    request<User>("/api/v1/auth/me", { headers: { Authorization: `Bearer ${token}` } }),
};

// ---------------------------------------------------------------- Stage 2: RAG
export interface KnowledgeBase {
  id: string;
  slug: string;
  name: string;
  description: string;
  visibility: "private" | "restricted" | "public";
  document_count: number;
  chunk_count: number;
  can_write: boolean;
}
export interface DocVersion {
  version: number;
  content_sha256: string;
  size_bytes: number;
  index_status: "pending" | "indexing" | "indexed" | "failed" | "superseded";
  error_message: string | null;
  chunk_count: number;
  embedding_model: string | null;
  created_at: string;
}
export interface DocumentInfo {
  id: string;
  kb_id: string;
  source_name: string;
  title: string;
  mime_type: string;
  status: string;
  current_version: number;
  latest_status: DocVersion["index_status"] | null;
  latest_error: string | null;
  chunk_count: number;
  updated_at: string;
  versions: DocVersion[] | null;
}
export interface UploadResult {
  status: "indexed" | "duplicate" | "failed";
  message: string;
  chunk_count: number;
  document: DocumentInfo;
}
export interface ChunkInfo {
  id: string;
  chunk_index: number;
  content: string;
  page_number: number | null;
  heading_path: string | null;
  char_start: number;
  char_end: number;
  chars: number;
  search_tokens: string[];
}
export type Mode = "vector" | "keyword" | "hybrid" | "hybrid_rerank";
export const MODES: Mode[] = ["vector", "keyword", "hybrid", "hybrid_rerank"];
type Methods = { vector: number | null; keyword: number | null; rrf: number | null; rerank: number | null };
export interface Candidate {
  chunk_id: string;
  rank: number;
  score: number;
  content: string;
  document_id: string;
  document_name: string;
  document_title: string;
  version: number;
  kb_slug: string;
  page_number: number | null;
  heading_path: string | null;
  char_start: number;
  char_end: number;
  suspicious: boolean;
  scores: Methods;
  ranks: Methods;
  locator?: string;
}
export interface SearchResult {
  query: string;
  mode: Mode;
  tokens: string[];
  candidates: Candidate[];
  timings_ms: Record<string, number>;
  embedding_model: string;
  reranker: string | null;
}
export interface Citation {
  index: number;
  chunk_id: string;
  document_id: string;
  document_name: string;
  kb_slug: string;
  version: number;
  page_number: number | null;
  heading_path: string | null;
  locator: string;
  evidence: number;
  quote: string;
}
export interface QAResult {
  question: string;
  answer: string;
  generator: string;
  is_mock: boolean;
  evidence_sufficient: boolean;
  evidence_score: number;
  threshold: number;
  citations: Citation[];
  flagged_chunks: { chunk_id: string; document_name: string; removed_lines: number }[];
  retrieval_mode: string;
  candidates: { chunk_id: string; rank: number; score: number; document_name: string; locator: string; evidence: number; suspicious: boolean }[];
  timings_ms: Record<string, number>;
}
export interface ModeMetrics {
  n: number;
  recall_at_5: number | null;
  mrr_at_10: number | null;
  ndcg_at_10: number | null;
  latency_p50_ms: number;
  latency_p95_ms: number;
  failure_rate: number;
  acl_leaked_chunks: number;
  by_category: Record<string, { n: number; recall_at_5: number | null; mrr_at_10: number | null }>;
  qa?: {
    no_answer_correct_refusal_rate: number | null;
    answerable_answer_rate: number | null;
    citation_acl_leaks: number;
    latency_p50_ms: number;
    latency_p95_ms: number;
  } | null;
}
export interface EvalRun {
  id: string;
  name: string;
  dataset_name: string;
  dataset_sha256: string;
  retriever: string;
  llm_provider: string;
  git_commit: string | null;
  config: Record<string, unknown> & { warning?: string | null; embedding_model?: string; embedding_has_semantics?: boolean };
  metrics: ModeMetrics;
  status: string;
  started_at: string;
  finished_at: string | null;
}
export interface EvalCase {
  case_id: string;
  label_source: string;
  recall_at_5: number | null;
  latency_ms: number | null;
  details: { category?: string; failure_category?: string; mrr_at_10?: number | null; acl_leaks?: number; top?: { rank: number; doc: string; page: number | null }[]; error?: string | null };
}

const auth = (token: string, extra?: HeadersInit): HeadersInit => ({ Authorization: `Bearer ${token}`, ...extra });
const json = (token: string, method: string, body: unknown): RequestInit => ({
  method,
  headers: auth(token, { "Content-Type": "application/json" }),
  body: JSON.stringify(body),
});

export const rag = {
  kbs: (t: string) => request<KnowledgeBase[]>("/api/v1/knowledge-bases", { headers: auth(t) }),
  documents: (t: string, kbId: string) =>
    request<DocumentInfo[]>(`/api/v1/knowledge-bases/${kbId}/documents`, { headers: auth(t) }),
  document: (t: string, id: string) => request<DocumentInfo>(`/api/v1/documents/${id}`, { headers: auth(t) }),
  upload: (t: string, kbId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<UploadResult>(`/api/v1/knowledge-bases/${kbId}/documents`, {
      method: "POST",
      headers: auth(t),
      body: fd,
    });
  },
  chunks: (t: string, id: string, offset = 0, limit = 20) =>
    request<{ total: number; items: ChunkInfo[] }>(`/api/v1/documents/${id}/chunks?offset=${offset}&limit=${limit}`, {
      headers: auth(t),
    }),
  reindex: (t: string, id: string) =>
    request<{ status: string; message: string; chunk_count: number }>(`/api/v1/documents/${id}/reindex`, {
      method: "POST",
      headers: auth(t),
    }),
  remove: (t: string, id: string) =>
    request<{ status: string; removed_chunks: number }>(`/api/v1/documents/${id}`, { method: "DELETE", headers: auth(t) }),
  search: (t: string, query: string, mode: Mode, top_k: number) =>
    request<SearchResult>("/api/v1/search", json(t, "POST", { query, mode, top_k })),
  qa: (t: string, question: string) => request<QAResult>("/api/v1/qa", json(t, "POST", { question })),
  evalRuns: (t: string) => request<EvalRun[]>("/api/v1/evals/runs", { headers: auth(t) }),
  evalRun: (t: string, id: string) =>
    request<EvalRun & { cases: EvalCase[] }>(`/api/v1/evals/runs/${id}`, { headers: auth(t) }),
};

// ---------------------------------------------------------------- Stage 3: Agent
export interface TraceNode {
  turn: number;
  node: string;
  status: string;
  ms: number;
  detail: Record<string, unknown>;
}
export interface AgentEvidence {
  id: string;
  source: "kb" | "tool";
  title: string;
  locator: string;
  flagged: boolean;
  score: number | null;
  snippet: string;
}
export interface AgentToolCall {
  tool: string;
  status: string;
  args: Record<string, unknown>;
  result: string | null;
  error_class: string | null;
  attempt: number;
  duration_ms: number;
  idempotency_key: string | null;
}
export interface AgentCitation {
  index: number;
  source: string;
  title: string;
  locator: string;
  id: string;
}
export interface AgentRun {
  run_id: string;
  thread_id: string;
  turn: number;
  status: RunStatus;
  question: string;
  intent: string | null;
  answer: string | null;
  citations: AgentCitation[];
  node_trace: TraceNode[];
  evidence: AgentEvidence[];
  assessment: { decision?: string; reasons?: string[]; missing?: string[]; contradictions?: string[] };
  refusal_reason: string | null;
  policy_violations: string[];
  degraded: string[];
  tools: AgentToolCall[];
  pending_approval: { type: string; draft_id: string; payload_sha256: string } | null;
  draft: {
    draft_id: string;
    status: string;
    title: string;
    priority: string;
    service: string | null;
    description?: string;
    revision?: number;
    parent_draft_id?: string | null;
  } | null;
  draft_result?: {
    status: string;
    decision: string | null;
    approval_id: string | null;
    reviewer_id: string | null;
    decided_at: string | null;
    revision?: number;
    ticket_number?: string | null;
    business_op?: string | null;
  } | null;
  usage: { tokens_in: number; tokens_out: number; cost_cny: number; source: string };
  latency_ms: number | null;
  failure_reason: string | null;
  is_mock: boolean;
  llm: string;
  created_at: string;
}

const authJson = (token: string, body?: unknown): RequestInit => ({
  method: body === undefined ? "GET" : "POST",
  headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export type RunStatus = "running" | "awaiting_approval" | "completed" | "refused" | "failed" | "expired" | "aborted";
export interface RunListItem {
  run_id: string;
  thread_id: string;
  turn: number;
  status: RunStatus;
  question: string;
  is_mock: boolean;
  created_at: string;
}
export interface ThreadItem {
  thread_id: string;
  last_status: RunStatus;
  last_run_id: string;
  updated_at: string;
  turns: number;
  title: string;
  is_mock: boolean;
}
const post = (token: string, path: string, body: unknown = {}) => request<AgentRun>(path, authJson(token, body));

export const agent = {
  run: (token: string, question: string, thread_id?: string, stream = false) =>
    request<AgentRun>("/api/v1/agent/runs", authJson(token, { question, thread_id, stream })),
  list: (token: string) => request<RunListItem[]>("/api/v1/agent/runs", authJson(token)),
  threads: (token: string) => request<ThreadItem[]>("/api/v1/agent/threads", authJson(token)),
  thread: (token: string, id: string) => request<AgentRun[]>(`/api/v1/agent/threads/${encodeURIComponent(id)}`, authJson(token)),
  cancel: (token: string, id: string) =>
    request<{ cancel_requested: boolean }>(`/api/v1/agent/runs/${id}/cancel`, authJson(token, {})),
  resume: (token: string, id: string, stream = false) => post(token, `/api/v1/agent/runs/${id}/resume?stream=${stream}`),
  history: (token: string, id: string) =>
    request<{ checkpoint_id: string; step: number; source: string; next: string[]; created_at: string }[]>(
      `/api/v1/agent/runs/${id}/history`,
      authJson(token),
    ),
  get: (token: string, id: string) => request<AgentRun>(`/api/v1/agent/runs/${id}`, authJson(token)),
  approve: (token: string, id: string, decision: Decision, comment = "", stream = false) =>
    request<AgentRun>(`/api/v1/agent/runs/${id}/approval${stream ? "?stream=true" : ""}`, authJson(token, { decision, comment })),
  approvals: (token: string) => request<AgentRun[]>("/api/v1/agent/approvals", authJson(token)),
};

// ---------------------------------------------------------------- Stage 4: MCP / tickets
export type Decision = "approved" | "rejected" | "returned";
export interface TicketEventView {
  from_status: string | null;
  to_status: string;
  actor_id: string | null;
  approver_id: string | null;
  actor_name?: string | null;
  approver_name?: string | null;
  approval_id: string | null;
  approved_at: string | null;
  note: string | null;
  created_at: string;
}
export interface TicketView {
  number: string;
  title: string;
  priority: string;
  status: string;
  service: string | null;
  category: string;
  source_draft_id: string | null;
  created_at: string;
  updated_at?: string;
  description?: string;
  resolution?: string | null;
  events: TicketEventView[];
}
export interface McpAuditRow {
  side: "server" | "client";
  server: string;
  tool: string;
  transport: string;
  user_role: string | null;
  thread_id: string | null;
  args_summary: Record<string, unknown> | null;
  args_sha256: string | null;
  status: string;
  error_class: string | null;
  error: string | null;
  duration_ms: number | null;
  output_bytes: number | null;
  created_at: string;
}
export const mcp = {
  ticket: (token: string, n: string) => request<TicketView>(`/api/v1/tickets/${encodeURIComponent(n)}`, authJson(token)),
  audit: (token: string, threadId: string) =>
    request<McpAuditRow[]>(`/api/v1/mcp/audit?thread_id=${encodeURIComponent(threadId)}&limit=100`, authJson(token)),
};

// ---------------------------------------------------------------- Stage 5: platform
export interface Ratio {
  value: number | null;
  numerator: number;
  denominator: number;
  definition?: string;
  source?: string;
}
export interface UsageBucket {
  runs: number;
  tokens_in: number;
  tokens_out: number;
  cost_cny: number;
}
export interface Dashboard {
  scope: string;
  generated_at: string;
  tickets: { total: number; pending: number; completed: number; by_status: Record<string, number>; pending_drafts: number; source: string };
  agent_runs: {
    total: number;
    by_status: Record<string, number>;
    task_success: Ratio;
    daily: { day: string; runs: number; completed: number }[];
    latency: { p50_ms: number | null; p95_ms: number | null; n: number; source: string };
  };
  tool_success: Ratio & { by_status: Record<string, number> };
  usage: { mock: UsageBucket; real: UsageBucket; note: string; source: string };
  recent_runs: { run_id: string; thread_id: string; status: RunStatus; question: string; intent: string | null; latency_ms: number | null; is_mock: boolean; llm: string; created_at: string }[];
}
export interface DraftRow {
  draft_id: string;
  run_id: string | null;
  action: string;
  title: string;
  priority: string;
  service: string | null;
  status: string;
  revision: number;
  parent_draft_id: string | null;
  created_by: string | null;
  created_at: string;
  evidence_count: number;
  approval: { decision: string; reviewer: string | null; comment: string | null; decided_at: string } | null;
}
export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  role_label: string;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}
export interface AuditRow {
  id: number;
  actor: string | null;
  action: string;
  target_type: string;
  target_id: string;
  detail: Record<string, unknown>;
  created_at: string;
}
export interface SettingsView {
  version: string;
  stage: number;
  env: string;
  models: Record<string, string | number | boolean>;
  runtime: Record<string, string | number | boolean>;
  budget: {
    allow_paid_api: boolean;
    paid_budget_cny: number;
    paid_max_calls: number;
    paid_approval_configured: boolean;
    ledger: { path_configured: boolean; entries: number | null; cost_cny: number | null };
  };
  health: Readiness;
}
export interface ObservabilityView {
  counters: Record<string, number>;
  latency_ms: Record<
    string,
    { count: number; p50: number | null; p95: number | null; p99: number | null; max: number | null }
  >;
  recent_spans: {
    name: string;
    kind: string;
    trace_id: string;
    request_id?: string | null;
    thread_id?: string | null;
    run_id?: string | null;
    status: string;
    duration_ms: number;
    error_type?: string | null;
    attrs?: Record<string, unknown>;
    ts: number;
  }[];
  note: string;
  durable: {
    agent_runs_last_hour: number;
    mcp_calls_last_hour: number;
    mcp_errors_last_hour: number;
    recent_runs: {
      run_id: string;
      thread_id: string | null;
      status: string;
      latency_ms: number | null;
      is_mock: boolean;
      created_at: string | null;
    }[];
  };
  viewer: { email: string; role: string };
}

export interface KbPermissions {
  kb_id: string;
  slug: string;
  visibility: "public" | "restricted";
  owner: { id: string; display_name: string | null };
  grants: { user_id: string; email: string; display_name: string; permission: "read" | "write" | "admin" }[];
}
export interface DirectoryUser {
  id: string;
  display_name: string;
  email: string;
  role: Role;
}
export interface EvalRow {
  kind: "rag" | "agent";
  file: string;
  generated_at: string | null;
  dataset: string | null;
  split: string | null;
  dataset_sha256: string | null;
  label_source: string | null;
  version: string | null;
  model: string | null;
  provider: string | null;
  is_mock: boolean;
  environment: string;
  generator: string | null;
  n: number | null;
  mode: string;
  disclaimer?: string | null;
  metrics: Record<string, number | null>;
}
export interface TicketFilter {
  q?: string;
  status?: string;
  priority?: string;
}

const qs = (o: Record<string, string | number | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};
const send = (token: string, method: string, body: unknown): RequestInit => ({
  method,
  headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const get = (token: string): RequestInit => ({ headers: { Authorization: `Bearer ${token}` } });

export const platform = {
  dashboard: (t: string) => request<Dashboard>("/api/v1/dashboard/summary", get(t)),
  tickets: (t: string, f: TicketFilter = {}) => request<TicketView[]>(`/api/v1/tickets${qs({ limit: 100, ...f })}`, get(t)),
  ticket: (t: string, n: string) => request<TicketView>(`/api/v1/tickets/${encodeURIComponent(n)}`, get(t)),
  transitions: (t: string) => request<Record<string, string[]>>("/api/v1/ticket-transitions", get(t)),
  transition: (t: string, n: string, body: { status: string; note: string; resolution?: string }) =>
    request<TicketView>(`/api/v1/tickets/${encodeURIComponent(n)}`, send(t, "PATCH", body)),
  drafts: (t: string, status?: string) => request<DraftRow[]>(`/api/v1/ticket-drafts${qs({ status })}`, get(t)),
  directory: (t: string) => request<DirectoryUser[]>("/api/v1/users/directory", get(t)),
  users: (t: string) => request<AdminUser[]>("/api/v1/admin/users", get(t)),
  roles: (t: string) => request<{ role: Role; label: string; capabilities: string[] }[]>("/api/v1/admin/roles", get(t)),
  createUser: (t: string, body: { email: string; display_name: string; role: Role; password: string }) =>
    request<AdminUser>("/api/v1/admin/users", send(t, "POST", body), [201]),
  updateUser: (t: string, id: string, body: Partial<{ display_name: string; role: Role; is_active: boolean; password: string }>) =>
    request<AdminUser>(`/api/v1/admin/users/${id}`, send(t, "PATCH", body)),
  audit: (t: string) => request<AuditRow[]>("/api/v1/admin/audit", get(t)),
  settings: (t: string) => request<SettingsView>("/api/v1/admin/settings", get(t)),
  observability: (t: string) => request<ObservabilityView>("/api/v1/admin/observability", get(t)),
  createKb: (t: string, body: { slug: string; name: string; description: string; visibility: "public" | "restricted" }) =>
    request<{ id: string; slug: string }>("/api/v1/knowledge-bases", send(t, "POST", body), [201]),
  deleteKb: (t: string, id: string, confirm_slug: string) =>
    request<{ status: string; slug: string; documents: number; chunks_removed: number }>(
      `/api/v1/knowledge-bases/${id}`,
      send(t, "DELETE", { confirm_slug }),
    ),
  kbPermissions: (t: string, id: string) => request<KbPermissions>(`/api/v1/knowledge-bases/${id}/permissions`, get(t)),
  putKbPermissions: (t: string, id: string, body: { visibility: string; grants: { user_id: string; permission: string }[] }) =>
    request<KbPermissions>(`/api/v1/knowledge-bases/${id}/permissions`, send(t, "PUT", body)),
  evalResults: (t: string) =>
    request<{ source: string; available: boolean; rows: EvalRow[]; skipped_files: string[]; note: string }>("/api/v1/evals/results", get(t)),
};
