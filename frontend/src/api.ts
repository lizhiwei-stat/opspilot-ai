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
  role: "user" | "admin";
}
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

const BASE = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(path: string, init?: RequestInit, okStatuses = [200]): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ApiError(0, "network_error", "无法连接后端服务", null);
  }
  const body = await res.json().catch(() => null);
  if (!okStatuses.includes(res.status)) {
    const e = body?.error;
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
  status: "running" | "awaiting_approval" | "completed" | "refused" | "failed" | "expired";
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
  draft: { draft_id: string; status: string; title: string; priority: string; service: string | null } | null;
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

export const agent = {
  run: (token: string, question: string, thread_id?: string) =>
    request<AgentRun>("/api/v1/agent/runs", authJson(token, { question, thread_id })),
  get: (token: string, id: string) => request<AgentRun>(`/api/v1/agent/runs/${id}`, authJson(token)),
  approve: (token: string, id: string, decision: "approved" | "rejected", comment = "") =>
    request<AgentRun>(`/api/v1/agent/runs/${id}/approval`, authJson(token, { decision, comment })),
};
