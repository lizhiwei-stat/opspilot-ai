# 检索评测结果

- 时间(UTC)：2026-11-02T05:19:10.481277+00:00
- commit：`4b5fd60429d2677ea916a77cc9816a7dd2559f28+dirty`
- 数据集：retrieval_zh_heldout.jsonl（split=heldout，37 条，label_source=synthetic，sha256 `439a59a159bd`）
- Embedding：local / bge-small-zh-v1.5（revision `7999e1d33597`，dim 512）；QA 生成器：mock-extractive
- Reranker：rule=lexical-overlap-v1(rule)
- 配置：`{"chunk_size": 500, "chunk_overlap": 80, "rrf_k": 60, "rerank_fusion": "replace", "rerank_fusion_weight": 0.5, "retrieval_candidates": 50, "rerank_candidates": 20, "qa_evidence": "lexical", "qa_min_evidence": 0.5, "qa_min_rerank_score": 0.02, "qa_require_anchor_terms": false, "hnsw_iterative_scan": "relaxed_order", "hnsw_ef_search": 100, "top_k": 10}`

| 模式 | Recall@5 | MRR@10 | nDCG@10 | P50 ms | P95 ms | 失败率 | ACL 泄露块数 |
|---|---|---|---|---|---|---|---|
| vector | 0.889 | 0.796 | 0.819 | 11.92 | 14.05 | 0.0 | 0 |
| keyword | 0.963 | 0.867 | 0.892 | 2.75 | 3.37 | 0.0 | 0 |
| hybrid | 0.926 | 0.869 | 0.893 | 13.01 | 14.76 | 0.0 | 0 |
| hybrid_rerank:rule | 0.963 | 0.920 | 0.925 | 16.48 | 20.13 | 0.0 | 0 |

按类别 Recall@5（无答案/无权限类无 gold，不计）：

| 模式 | error_code | exact_keyword | multi_doc | no_answer | no_permission | paraphrase |
|---|---|---|---|---|---|---|
| vector | 1.000 | 1.000 | 0.600 | - | - | 0.938 |
| keyword | 1.000 | 1.000 | 0.800 | - | - | 1.000 |
| hybrid | 1.000 | 1.000 | 0.800 | - | - | 0.938 |
| hybrid_rerank:rule | 1.000 | 1.000 | 1.000 | - | - | 0.938 |

QA（hybrid_rerank:rule，mock-extractive，profile=stage2，证据信号 lexical，词面阈值 0.5 / 重排阈值 0.02，anchor=False）：
- 无答案/无权限问题正确拒答率：0.700
- 可回答问题的作答率：0.667
- 引用 ACL 泄露：0
- QA 延迟 P50/P95：17.59 / 21.25 ms
