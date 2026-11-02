# 检索评测结果

- 时间(UTC)：2026-11-02T05:19:23.783669+00:00
- commit：`4b5fd60429d2677ea916a77cc9816a7dd2559f28+dirty`
- 数据集：retrieval_zh.jsonl（split=dev，55 条，label_source=synthetic，sha256 `5ffd852d48c9`）
- Embedding：local / bge-small-zh-v1.5（revision `7999e1d33597`，dim 512）；QA 生成器：None
- Reranker：cross_encoder=cross-encoder:bge-reranker-base(model)
- 配置：`{"chunk_size": 500, "chunk_overlap": 80, "rrf_k": 60, "rerank_fusion": "replace", "rerank_fusion_weight": 0.5, "retrieval_candidates": 50, "rerank_candidates": 20, "qa_evidence": "auto", "qa_min_evidence": 0.25, "qa_min_rerank_score": 0.02, "qa_require_anchor_terms": true, "hnsw_iterative_scan": "relaxed_order", "hnsw_ef_search": 100, "top_k": 10}`

| 模式 | Recall@5 | MRR@10 | nDCG@10 | P50 ms | P95 ms | 失败率 | ACL 泄露块数 |
|---|---|---|---|---|---|---|---|
| hybrid_rerank:cross_encoder | 0.992 | 0.961 | 0.958 | 1951.44 | 2093.54 | 0.0 | 0 |

按类别 Recall@5（无答案/无权限类无 gold，不计）：

| 模式 | error_code | exact_keyword | multi_doc | no_answer | no_permission | paraphrase |
|---|---|---|---|---|---|---|
| hybrid_rerank:cross_encoder | 1.000 | 1.000 | 0.952 | - | - | 1.000 |
