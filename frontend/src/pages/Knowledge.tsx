import { useCallback, useEffect, useState } from "react";
import { type ChunkInfo, type DocumentInfo, type KnowledgeBase, rag } from "../api";
import { Badge, Card, ErrorText, STATUS_LABEL, STATUS_TONE, btn, btnPrimary } from "../components/ui";
import { errText } from "../errors";

const VIS_LABEL = { public: "公开", restricted: "受限", private: "私有" } as const;

export default function Knowledge({ token }: { token: string }) {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [kbId, setKbId] = useState<string | null>(null);
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [selected, setSelected] = useState<DocumentInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const kb = kbs.find((k) => k.id === kbId) ?? null;

  const loadKbs = useCallback(async () => {
    try {
      const list = await rag.kbs(token);
      setKbs(list);
      setKbId((cur) => cur ?? list[0]?.id ?? null);
    } catch (e) {
      setError(errText(e));
    }
  }, [token]);

  const loadDocs = useCallback(async () => {
    if (!kbId) return;
    try {
      setDocs(await rag.documents(token, kbId));
    } catch (e) {
      setError(errText(e));
    }
  }, [token, kbId]);

  useEffect(() => void loadKbs(), [loadKbs]);
  useEffect(() => {
    setSelected(null);
    void loadDocs();
  }, [loadDocs]);

  async function act(fn: () => Promise<string>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setNotice(await fn());
      await Promise.all([loadDocs(), loadKbs()]);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function onUpload(file: File | undefined) {
    if (!file || !kbId) return;
    await act(async () => {
      const r = await rag.upload(token, kbId, file);
      if (r.status === "failed") throw new Error(`上传失败：${r.message}`);
      return `${file.name}：${STATUS_LABEL[r.status]} · ${r.message}`;
    });
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
      <Card title="知识库" label="kb-list">
        <ul className="space-y-1">
          {kbs.map((k) => (
            <li key={k.id}>
              <button
                onClick={() => setKbId(k.id)}
                className={`w-full rounded-md px-3 py-2 text-left text-sm ${k.id === kbId ? "bg-indigo-50 text-indigo-800" : "hover:bg-slate-50"}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{k.name}</span>
                  <Badge tone={k.visibility === "public" ? "green" : "amber"}>{VIS_LABEL[k.visibility]}</Badge>
                </div>
                <div className="text-xs text-slate-500">
                  {k.slug} · {k.document_count} 文档 · {k.chunk_count} 分块
                </div>
              </button>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-slate-500">只显示你有读权限的知识库（ACL 在数据库查询中过滤）。</p>
      </Card>

      <div className="space-y-6">
        <Card
          title={kb ? `${kb.name} · 文档` : "文档"}
          label="documents"
          actions={
            kb?.can_write ? (
              <label className={`${btnPrimary} cursor-pointer`}>
                {busy ? "处理中…" : "上传文档"}
                <input
                  type="file"
                  aria-label="上传文件"
                  accept=".pdf,.md,.markdown,.txt"
                  className="hidden"
                  disabled={busy}
                  onChange={(e) => {
                    void onUpload(e.target.files?.[0]);
                    e.target.value = "";
                  }}
                />
              </label>
            ) : (
              <Badge>只读</Badge>
            )
          }
        >
          <ErrorText error={error} />
          {notice && <p className="mb-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{notice}</p>}
          <p className="mb-3 text-xs text-slate-500">支持 PDF / Markdown / TXT，单文件上限 10 MB；内容相同的文件按 SHA-256 去重。</p>
          {docs.length === 0 ? (
            <p className="text-sm text-slate-500">暂无文档</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th className="py-2">文件</th>
                  <th>版本</th>
                  <th>状态</th>
                  <th>分块</th>
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id} className={`border-t ${selected?.id === d.id ? "bg-indigo-50/50" : ""}`}>
                    <td className="py-2">
                      <button className="text-left font-medium text-indigo-700 hover:underline" onClick={() => void rag.document(token, d.id).then(setSelected).catch((e) => setError(errText(e)))}>
                        {d.source_name}
                      </button>
                      {d.latest_error && <div className="text-xs text-rose-600">{d.latest_error}</div>}
                    </td>
                    <td>v{d.current_version}</td>
                    <td>
                      {d.latest_status && <Badge tone={STATUS_TONE[d.latest_status]}>{STATUS_LABEL[d.latest_status]}</Badge>}
                    </td>
                    <td>{d.chunk_count}</td>
                    <td className="space-x-2 text-right">
                      {kb?.can_write && (
                        <>
                          <button className={btn} disabled={busy} onClick={() => void act(async () => (await rag.reindex(token, d.id)).message)}>
                            重建索引
                          </button>
                          <button
                            className={`${btn} text-rose-700`}
                            disabled={busy}
                            onClick={() => {
                              if (window.confirm(`删除 ${d.source_name}？其全部分块会被移除。`))
                                void act(async () => `已删除，移除 ${(await rag.remove(token, d.id)).removed_chunks} 个分块`);
                            }}
                          >
                            删除
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        {selected && <DocumentDetail token={token} doc={selected} />}
      </div>
    </div>
  );
}

function DocumentDetail({ token, doc }: { token: string; doc: DocumentInfo }) {
  const [chunks, setChunks] = useState<ChunkInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const page = 10;

  useEffect(() => {
    rag
      .chunks(token, doc.id, offset, page)
      .then((r) => {
        setChunks(r.items);
        setTotal(r.total);
      })
      .catch((e) => setError(errText(e)));
  }, [token, doc.id, offset]);
  useEffect(() => setOffset(0), [doc.id]);

  return (
    <Card title={`${doc.source_name} · 版本与分块`} label="chunk-viewer">
      <ErrorText error={error} />
      <h3 className="mb-2 text-sm font-medium">版本历史</h3>
      <table className="mb-5 w-full text-xs">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="py-1">版本</th>
            <th>状态</th>
            <th>分块</th>
            <th>Embedding 模型</th>
            <th>SHA-256</th>
            <th>大小</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          {doc.versions?.map((v) => (
            <tr key={v.version} className="border-t align-top">
              <td className="py-1">v{v.version}</td>
              <td>
                <Badge tone={STATUS_TONE[v.index_status]}>{STATUS_LABEL[v.index_status]}</Badge>
                {v.error_message && <div className="text-rose-600">{v.error_message}</div>}
              </td>
              <td>{v.chunk_count}</td>
              <td className="font-mono">{v.embedding_model ?? "–"}</td>
              <td className="font-mono" title={v.content_sha256}>
                {v.content_sha256.slice(0, 12)}
              </td>
              <td>{(v.size_bytes / 1024).toFixed(1)} KB</td>
              <td>{new Date(v.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-medium">分块（共 {total} 个，当前版本 v{doc.current_version}）</h3>
        <div className="space-x-2">
          <button className={btn} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - page))}>
            上一页
          </button>
          <button className={btn} disabled={offset + page >= total} onClick={() => setOffset(offset + page)}>
            下一页
          </button>
        </div>
      </div>
      <ol className="space-y-3">
        {chunks.map((c) => (
          <li key={c.id} className="rounded-lg border border-slate-200 p-3">
            <div className="mb-1 flex flex-wrap gap-2 text-xs text-slate-500">
              <Badge tone="indigo">#{c.chunk_index}</Badge>
              {c.page_number && <Badge tone="sky">第 {c.page_number} 页</Badge>}
              {c.heading_path && <span>{c.heading_path}</span>}
              <span>
                字符 {c.char_start}–{c.char_end}（{c.chars} 字）
              </span>
            </div>
            <pre className="whitespace-pre-wrap font-sans text-sm text-slate-800">{c.content}</pre>
            <div className="mt-2 text-xs text-slate-400" title="jieba 分词后写入 search_text 的前 40 个词">
              关键词索引：{c.search_tokens.join(" / ")}
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}
