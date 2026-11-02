# 检索评测结果

- 时间(UTC)：2026-11-02T05:18:55.817567+00:00
- commit：`4b5fd60429d2677ea916a77cc9816a7dd2559f28+dirty`
- 数据集：retrieval_zh.jsonl（split=dev，55 条，label_source=synthetic，sha256 `5ffd852d48c9`）
- Embedding：local / bge-small-zh-v1.5（revision `7999e1d33597`，dim 512）；QA 生成器：mock-extractive
- Reranker：rule=lexical-overlap-v1(rule)
- 配置：`{"chunk_size": 500, "chunk_overlap": 80, "rrf_k": 60, "rerank_fusion": "replace", "rerank_fusion_weight": 0.5, "retrieval_candidates": 50, "rerank_candidates": 20, "qa_evidence": "lexical", "qa_min_evidence": 0.5, "qa_min_rerank_score": 0.02, "qa_require_anchor_terms": false, "hnsw_iterative_scan": "relaxed_order", "hnsw_ef_search": 100, "top_k": 10}`

| 模式 | Recall@5 | MRR@10 | nDCG@10 | P50 ms | P95 ms | 失败率 | ACL 泄露块数 |
|---|---|---|---|---|---|---|---|
| vector | 0.891 | 0.810 | 0.843 | 11.34 | 13.89 | 0.0 | 0 |
| keyword | 0.934 | 0.836 | 0.870 | 2.41 | 3.29 | 0.0 | 0 |
| hybrid | 0.954 | 0.893 | 0.906 | 12.36 | 14.01 | 0.0 | 0 |
| hybrid_rerank:rule | 0.957 | 0.878 | 0.901 | 15.55 | 18.89 | 0.0 | 0 |

按类别 Recall@5（无答案/无权限类无 gold，不计）：

| 模式 | error_code | exact_keyword | multi_doc | no_answer | no_permission | paraphrase |
|---|---|---|---|---|---|---|
| vector | 0.767 | 0.900 | 0.881 | - | - | 0.969 |
| keyword | 0.967 | 0.950 | 1.000 | - | - | 0.875 |
| hybrid | 0.900 | 1.000 | 0.929 | - | - | 0.969 |
| hybrid_rerank:rule | 1.000 | 1.000 | 0.881 | - | - | 0.938 |

QA（hybrid_rerank:rule，mock-extractive，profile=stage2，证据信号 lexical，词面阈值 0.5 / 重排阈值 0.02，anchor=False）：
- 无答案/无权限问题正确拒答率：0.833
- 可回答问题的作答率：0.744
- 引用 ACL 泄露：0
- QA 延迟 P50/P95：16.63 / 19.4 ms
