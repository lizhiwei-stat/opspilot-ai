# 检索评测结果

- 时间(UTC)：2026-11-02T05:57:41.832009+00:00
- commit：`ad162ac178ade022ffef831afb07a6dbc3b8e2d4+dirty`
- 数据集：retrieval_zh_heldout.jsonl（split=heldout，37 条，label_source=synthetic，sha256 `439a59a159bd`）
- Embedding：openai_compatible / text-embedding-v4（revision `None`，dim 1024）；QA 生成器：mock-extractive
- Reranker：rule=lexical-overlap-v1(rule), cross_encoder=cross-encoder:bge-reranker-base(model)
- 配置：`{"chunk_size": 500, "chunk_overlap": 80, "rrf_k": 60, "rerank_fusion": "rrf", "rerank_fusion_weight": 0.5, "retrieval_candidates": 50, "rerank_candidates": 20, "qa_evidence": "auto", "qa_min_evidence": 0.25, "qa_min_rerank_score": 0.02, "qa_require_anchor_terms": true, "hnsw_iterative_scan": "relaxed_order", "hnsw_ef_search": 100, "top_k": 10}`

| 模式 | Recall@5 | MRR@10 | nDCG@10 | P50 ms | P95 ms | 失败率 | ACL 泄露块数 |
|---|---|---|---|---|---|---|---|
| vector | 0.907 | 0.842 | 0.860 | 783.46 | 1845.72 | 0.0 | 0 |
| keyword | 0.963 | 0.867 | 0.892 | 2.56 | 3.5 | 0.0 | 0 |
| hybrid | 0.944 | 0.883 | 0.906 | 760.71 | 1190.19 | 0.0 | 0 |
| hybrid_rerank:rule | 0.963 | 0.935 | 0.939 | 774.59 | 1696.29 | 0.0 | 0 |
| hybrid_rerank:cross_encoder | 1.000 | 0.957 | 0.966 | 2523.49 | 3722.05 | 0.0 | 0 |

按类别 Recall@5（无答案/无权限类无 gold，不计）：

| 模式 | error_code | exact_keyword | multi_doc | no_answer | no_permission | paraphrase |
|---|---|---|---|---|---|---|
| vector | 1.000 | 1.000 | 0.700 | - | - | 0.938 |
| keyword | 1.000 | 1.000 | 0.800 | - | - | 1.000 |
| hybrid | 1.000 | 1.000 | 0.900 | - | - | 0.938 |
| hybrid_rerank:rule | 1.000 | 1.000 | 1.000 | - | - | 0.938 |
| hybrid_rerank:cross_encoder | 1.000 | 1.000 | 1.000 | - | - | 1.000 |

QA（hybrid_rerank:cross_encoder，mock-extractive，profile=current，证据信号 auto，词面阈值 0.25 / 重排阈值 0.02，anchor=True）：
- 无答案/无权限问题正确拒答率：0.500
- 可回答问题的作答率：1.000
- 引用 ACL 泄露：0
- QA 延迟 P50/P95：2484.74 / 3127.19 ms
