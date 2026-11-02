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
