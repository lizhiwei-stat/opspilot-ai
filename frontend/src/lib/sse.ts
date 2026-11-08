// SSE client over fetch() so the bearer token travels in the Authorization header (the
// browser EventSource API cannot set headers, and tokens in URLs leak into logs).
// Semantics follow the SSE spec: `id:` advances the cursor, reconnects send Last-Event-ID,
// `retry:` sets the base delay. This is node/event streaming - NOT token streaming.
import { BASE, UNAUTHORIZED_EVENT } from "../api";

export interface AgentEvent {
  seq: number;
  type: "run_started" | "node" | "tool" | "evidence" | "interrupt" | "cancel_requested" | "final";
  data: Record<string, unknown>;
  at: string;
}
export type StreamState =
  | { kind: "connecting"; attempt: number }
  | { kind: "open" }
  | { kind: "reconnecting"; attempt: number; delayMs: number; reason: string }
  | { kind: "ended"; status: string }
  | { kind: "failed"; reason: string };

export interface StreamOptions {
  token: string;
  runId: string;
  after?: number;
  onEvent: (e: AgentEvent) => void;
  onState?: (s: StreamState) => void;
  maxAttempts?: number;
  fetchImpl?: typeof fetch;
}

/** Parse complete SSE blocks out of `buf`; returns [events, remainder]. */
export function parseSSE(buf: string): [{ id?: string; event?: string; data?: string; retry?: number }[], string] {
  const norm = buf.replace(/\r\n/g, "\n");
  const parts = norm.split("\n\n");
  const rest = parts.pop() ?? "";
  const out = parts.map((block) => {
    const ev: { id?: string; event?: string; data?: string; retry?: number } = {};
    for (const line of block.split("\n")) {
      if (!line || line.startsWith(":")) continue;
      const i = line.indexOf(":");
      const k = i < 0 ? line : line.slice(0, i);
      const v = i < 0 ? "" : line.slice(i + 1).replace(/^ /, "");
      if (k === "id") ev.id = v;
      else if (k === "event") ev.event = v;
      else if (k === "data") ev.data = ev.data === undefined ? v : `${ev.data}\n${v}`;
      else if (k === "retry" && /^\d+$/.test(v)) ev.retry = Number(v);
    }
    return ev;
  });
  return [out, rest];
}

/** Follow /agent/runs/{id}/events until `end`; reconnects with backoff from the last id.
 * Returns a stop() function (closing never cancels the run - it runs on the server). */
export function streamRunEvents(o: StreamOptions): () => void {
  const ctrl = { stopped: false, abort: null as AbortController | null };
  let cursor = o.after ?? 0;
  let retryBase = 1000;
  const max = o.maxAttempts ?? 8;
  const f = o.fetchImpl ?? fetch;

  const loop = async () => {
    let attempt = 0;
    while (!ctrl.stopped) {
      attempt += 1;
      o.onState?.({ kind: "connecting", attempt });
      const ac = new AbortController();
      ctrl.abort = ac;
      let reason: string;
      try {
        const res = await f(`${BASE}/api/v1/agent/runs/${o.runId}/events`, {
          headers: { Authorization: `Bearer ${o.token}`, Accept: "text/event-stream", "Last-Event-ID": String(cursor) },
          signal: ac.signal,
        });
        if (res.status === 401) {
          window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
          o.onState?.({ kind: "failed", reason: "unauthorized" });
          return;
        }
        if (res.status === 404 || res.status === 403) {
          o.onState?.({ kind: "failed", reason: `HTTP ${res.status}` });
          return;
        }
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
        o.onState?.({ kind: "open" });
        attempt = 0;
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const [events, rest] = parseSSE(buf);
          buf = rest;
          for (const ev of events) {
            if (ev.retry) retryBase = ev.retry;
            if (ev.event === "end") {
              const d = ev.data ? JSON.parse(ev.data) : {};
              o.onState?.({ kind: "ended", status: String(d.status ?? "") });
              return;
            }
            if (ev.event === "timeout") break;
            if (ev.id && ev.data) {
              const seq = Number(ev.id);
              if (seq <= cursor) continue; // dedupe on replay
              cursor = seq;
              o.onEvent(JSON.parse(ev.data) as AgentEvent);
            }
          }
        }
        reason = "stream closed";
      } catch (e) {
        if (ctrl.stopped) return;
        reason = e instanceof Error ? e.message : String(e);
      }
      if (ctrl.stopped) return;
      if (attempt >= max) {
        o.onState?.({ kind: "failed", reason });
        return;
      }
      const delayMs = Math.min(retryBase * 2 ** Math.max(0, attempt - 1), 15000);
      o.onState?.({ kind: "reconnecting", attempt, delayMs, reason });
      await new Promise((r) => setTimeout(r, delayMs));
    }
  };
  void loop();
  return () => {
    ctrl.stopped = true;
    ctrl.abort?.abort();
  };
}
