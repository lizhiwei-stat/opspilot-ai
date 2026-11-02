import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("App", () => {
  it("renders failing readiness checks from a 503 response", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/health/ready"))
        return respond(
          { status: "not_ready", checks: [{ name: "database", ok: false, latency_ms: 1, detail: "down" }] },
          503,
        );
      return respond({
        name: "OpsPilot AI", version: "0.1.0", stage: 2, env: "test", llm_provider: "mock",
        llm_model: "mock-llm-v1", embedding_provider: "mock", embedding_model: "m", embedding_dim: 1024,
      });
    });
    render(<App />);
    await waitFor(() => expect(screen.getByText("未就绪")).toBeTruthy());
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.getByText(/Stage 2/)).toBeTruthy();
  });

  it("shows a network error when the backend is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    render(<App />);
    await waitFor(() => expect(screen.getByText(/network_error/)).toBeTruthy());
  });

  it("protected tabs ask for login first", async () => {
    window.location.hash = "#qa";
    vi.spyOn(globalThis, "fetch").mockImplementation(() => respond({ status: "ready", checks: [] }));
    render(<App />);
    expect(await screen.findByText(/请先在/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "问答" }).getAttribute("aria-current")).toBe("page");
    window.location.hash = "";
  });
});
