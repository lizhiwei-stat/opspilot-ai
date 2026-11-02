import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Candidate, DocumentInfo, EvalRun, KnowledgeBase, QAResult } from "../api";
import { mockFetch } from "../test-utils";
import Evals from "./Evals";
import Knowledge from "./Knowledge";
import QA from "./QA";
import SearchDebug from "./SearchDebug";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const KB: KnowledgeBase = {
  id: "kb1", slug: "public-faq", name: "公共 FAQ", description: "", visibility: "public",
  document_count: 1, chunk_count: 2, can_write: true,
};
const DOC: DocumentInfo = {
  id: "d1", kb_id: "kb1", source_name: "faq.md", title: "faq", mime_type: "text/markdown", status: "active",
  current_version: 2, latest_status: "indexed", latest_error: null, chunk_count: 2, updated_at: "2026-11-02T00:00:00Z",
  versions: [
    { version: 2, content_sha256: "a".repeat(64), size_bytes: 2048, index_status: "indexed", error_message: null, chunk_count: 2, embedding_model: "mock-hash-embedding-v1", created_at: "2026-11-02T00:00:00Z" },
    { version: 1, content_sha256: "b".repeat(64), size_bytes: 1024, index_status: "superseded", error_message: null, chunk_count: 1, embedding_model: "mock-hash-embedding-v1", created_at: "2026-11-01T00:00:00Z" },
  ],
};

describe("Knowledge page", () => {
  it("lists KBs/documents, shows versions + chunks, uploads and surfaces parse errors", async () => {
    const calls: string[] = [];
    mockFetch((url, init) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/api/v1/knowledge-bases")) return { body: [KB] };
      if (url.endsWith("/knowledge-bases/kb1/documents") && init?.method === "POST")
        return { body: { status: "failed", message: "不支持的文件类型 .exe", chunk_count: 0, document: DOC } };
      if (url.endsWith("/knowledge-bases/kb1/documents")) return { body: [DOC] };
      if (url.endsWith("/documents/d1")) return { body: DOC };
      if (url.includes("/documents/d1/chunks"))
        return { body: { total: 1, items: [{ id: "c1", chunk_index: 0, content: "访问令牌有效期为 60 分钟", page_number: 3, heading_path: "令牌", char_start: 0, char_end: 14, chars: 14, search_tokens: ["访问", "令牌"] }] } };
      return undefined;
    });
    render(<Knowledge token="t" />);
    await screen.findByText("faq.md");
    expect(screen.getByText("已索引")).toBeTruthy();
    fireEvent.click(screen.getByText("faq.md"));
    const viewer = await screen.findByLabelText("chunk-viewer");
    expect(within(viewer).getByText("已被新版本替换")).toBeTruthy();
    await within(viewer).findByText("访问令牌有效期为 60 分钟");
    expect(within(viewer).getByText("第 3 页")).toBeTruthy();

    const file = new File(["MZ"], "evil.exe");
    fireEvent.change(screen.getByLabelText("上传文件"), { target: { files: [file] } });
    await screen.findByText(/上传失败：不支持的文件类型/);
    expect(calls).toContain("POST /api/v1/knowledge-bases/kb1/documents");
    expect(calls.every((c) => !c.includes("undefined"))).toBe(true);
  });

  it("hides write actions on read-only KBs", async () => {
    mockFetch((url) => {
      if (url.endsWith("/api/v1/knowledge-bases")) return { body: [{ ...KB, can_write: false }] };
      if (url.endsWith("/documents")) return { body: [DOC] };
      return undefined;
    });
    render(<Knowledge token="t" />);
    await screen.findByText("faq.md");
    expect(screen.getByText("只读")).toBeTruthy();
    expect(screen.queryByText("删除")).toBeNull();
  });
});

const cand = (mode: string): Candidate => ({
  chunk_id: `c-${mode}`, rank: 1, score: 0.5, content: "ERR_PAY_TIMEOUT 可在 30 秒后重试", document_id: "d", document_name: "codes.md",
  document_title: "codes", version: 1, kb_slug: "order-platform", page_number: null, heading_path: "错误码", char_start: 0, char_end: 10,
  suspicious: mode === "keyword", scores: { vector: 0.3, keyword: 0.2, rrf: null, rerank: null }, ranks: { vector: 1, keyword: 2, rrf: null, rerank: null },
});

describe("SearchDebug page", () => {
  it("runs all four modes and shows per-method scores + mock warning", async () => {
    const bodies: string[] = [];
    mockFetch((url, init) => {
      if (url.endsWith("/api/v1/search")) {
        bodies.push(String(init?.body));
        const mode = JSON.parse(String(init?.body)).mode;
        return { body: { query: "q", mode, tokens: ["errpaytimeout", "重试"], candidates: [cand(mode)], timings_ms: { total_ms: 3.2 }, embedding_model: "mock-hash-embedding-v1", reranker: null } };
      }
      return undefined;
    });
    render(<SearchDebug token="t" embeddingProvider="mock" />);
    expect(screen.getByText(/没有语义理解/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "检索" }));
    await screen.findByLabelText("results-hybrid_rerank");
    expect(bodies.map((b) => JSON.parse(b).mode).sort()).toEqual(["hybrid", "hybrid_rerank", "keyword", "vector"]);
    expect(screen.getAllByText(/vector: 0.3000 \(#1\)/)).toHaveLength(4);
    expect(within(screen.getByLabelText("results-keyword")).getByText("可疑内容")).toBeTruthy();
    expect(screen.getByText("errpaytimeout")).toBeTruthy();
  });
});

const QA_OK: QAResult = {
  question: "q", answer: "【Mock 抽取式回答 · 未调用大模型，以下内容为原文摘录】\n[1] 稍后重试", generator: "mock-extractive", is_mock: true,
  evidence_sufficient: true, evidence_score: 0.8, threshold: 0.5,
  citations: [{ index: 1, chunk_id: "chunk-123", document_id: "d", document_name: "codes.md", kb_slug: "order-platform", version: 1, page_number: 2, heading_path: null, locator: "codes.md v1 · p.2", evidence: 0.8, quote: "稍后重试" }],
  flagged_chunks: [{ chunk_id: "x", document_name: "vendor.md", removed_lines: 1 }], retrieval_mode: "hybrid_rerank", candidates: [], timings_ms: { total_ms: 9 },
};

describe("QA page", () => {
  it("renders mock label, citations and injection warning", async () => {
    mockFetch((url) => (url.endsWith("/api/v1/qa") ? { body: QA_OK } : undefined));
    render(<QA token="t" />);
    fireEvent.click(screen.getByRole("button", { name: "提问" }));
    await screen.findByText("生成器：mock-extractive");
    expect(screen.getByText(/没有调用大模型/)).toBeTruthy();
    const cites = screen.getByLabelText("citations");
    expect(within(cites).getByText("codes.md")).toBeTruthy();
    expect(within(cites).getByText("第 2 页")).toBeTruthy();
    expect(within(cites).getByText(/chunk-123/)).toBeTruthy();
    expect(screen.getByText(/疑似提示注入/)).toBeTruthy();
  });

  it("shows insufficient evidence without citations", async () => {
    mockFetch(() => ({ body: { ...QA_OK, answer: "证据不足：……", evidence_sufficient: false, citations: [], flagged_chunks: [] } }));
    render(<QA token="t" />);
    fireEvent.click(screen.getByRole("button", { name: "提问" }));
    await screen.findByText("证据不足：……");
    expect(screen.queryByLabelText("citations")).toBeNull();
  });

  it("shows API errors", async () => {
    mockFetch(() => ({ body: { error: { code: "unauthorized", message: "令牌无效" } }, status: 401 }));
    render(<QA token="t" />);
    fireEvent.click(screen.getByRole("button", { name: "提问" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("HTTP 401"));
  });
});

const RUN: EvalRun = {
  id: "r1", name: "retrieval-hybrid-mock", dataset_name: "retrieval_zh.jsonl", dataset_sha256: "0".repeat(64), retriever: "hybrid",
  llm_provider: "mock-extractive", git_commit: "abcdef1234", status: "completed", started_at: "2026-11-02T00:00:00Z", finished_at: null,
  config: { warning: "mock embedding 没有语义", embedding_model: "mock-hash-embedding-v1", embedding_has_semantics: false },
  metrics: { n: 55, recall_at_5: 0.9, mrr_at_10: 0.8, ndcg_at_10: 0.85, latency_p50_ms: 4, latency_p95_ms: 6, failure_rate: 0, acl_leaked_chunks: 0, by_category: {}, qa: null },
};

describe("Evals page", () => {
  it("reads saved runs from the API and drills into cases", async () => {
    mockFetch((url) => {
      if (url.endsWith("/api/v1/evals/runs")) return { body: [RUN] };
      if (url.endsWith("/api/v1/evals/runs/r1"))
        return { body: { ...RUN, cases: [{ case_id: "q001", label_source: "synthetic", recall_at_5: 1, latency_ms: 3, details: { category: "exact_keyword", mrr_at_10: 1, top: [{ rank: 1, doc: "guide.pdf", page: 2 }] } }] } };
      return undefined;
    });
    render(<Evals token="t" />);
    await screen.findByText("0.900");
    expect(screen.getByText("mock embedding 没有语义")).toBeTruthy();
    expect(screen.getByText("无语义")).toBeTruthy();
    fireEvent.click(screen.getByText("hybrid"));
    await screen.findByText("q001");
    expect(screen.getByText("guide.pdf#p2")).toBeTruthy();
  });
});
