import { vi } from "vitest";

export type Route = (url: string, init?: RequestInit) => { body: unknown; status?: number } | undefined;

/** Route fetch calls to canned JSON by URL; records calls for assertions. Unmatched -> 404. */
export function mockFetch(route: Route) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const r = route(String(input), init as RequestInit | undefined);
    if (!r) return Promise.resolve(new Response(JSON.stringify({ error: { code: "not_found", message: "nf" } }), { status: 404 }));
    return Promise.resolve(new Response(JSON.stringify(r.body), { status: r.status ?? 200 }));
  });
}
