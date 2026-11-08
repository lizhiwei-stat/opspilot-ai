import { describe, expect, it, vi } from "vitest";
import { type AgentEvent, type StreamState, parseSSE, streamRunEvents } from "./sse";

const ev = (seq: number, type: string, data: object = {}) =>
  `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify({ seq, type, data, at: "t" })}\n\n`;

function sse(chunks: string[], fail = false): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      if (fail) setTimeout(() => c.error(new TypeError("network down")), 20);
      else c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

describe("parseSSE", () => {
  it("splits blocks, keeps the partial remainder, skips comments, reads retry", () => {
    const [evs, rest] = parseSSE("retry: 1500\n\n: hb\n\nid: 1\nevent: node\ndata: {\"a\":1}\n\nid: 2\nevent: no");
    expect(evs).toEqual([{ retry: 1500 }, {}, { id: "1", event: "node", data: '{"a":1}' }]);
    expect(rest).toBe("id: 2\nevent: no");
  });
});

describe("streamRunEvents", () => {
  it("reconnects with Last-Event-ID after a drop, dedupes replays and stops at end", async () => {
    const calls: Record<string, string>[] = [];
    const fetchImpl = vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) => {
      calls.push(init!.headers as Record<string, string>);
      if (calls.length === 1) return sse(["retry: 10\n\n", ev(1, "run_started"), ev(2, "node", { node: "guard_in" })], true);
      // server replays from the cursor; a duplicate id 2 must be ignored
      return sse([ev(2, "node", { node: "guard_in" }), ev(3, "final", { status: "completed" }), 'event: end\ndata: {"status":"completed"}\n\n']);
    }) as unknown as typeof fetch;
    const got: AgentEvent[] = [];
    const states: StreamState[] = [];
    await new Promise<void>((done) => {
      streamRunEvents({
        token: "tok",
        runId: "r1",
        fetchImpl,
        onEvent: (e) => got.push(e),
        onState: (s) => {
          states.push(s);
          if (s.kind === "ended") done();
        },
      });
    });
    expect(got.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(calls[0]["Last-Event-ID"]).toBe("0");
    expect(calls[1]["Last-Event-ID"]).toBe("2");
    expect(calls[0].Authorization).toBe("Bearer tok");
    expect(states.map((s) => s.kind)).toEqual(["connecting", "open", "reconnecting", "connecting", "open", "ended"]);
  });

  it("gives up on 404 without retrying", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 404 })) as unknown as typeof fetch;
    const s = await new Promise<StreamState>((done) =>
      streamRunEvents({ token: "t", runId: "x", fetchImpl, onEvent: () => {}, onState: (st) => st.kind === "failed" && done(st) }),
    );
    expect(s).toEqual({ kind: "failed", reason: "HTTP 404" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
