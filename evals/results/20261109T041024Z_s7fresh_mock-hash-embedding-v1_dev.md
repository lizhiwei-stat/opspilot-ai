# 检索评测结果

- 时间(UTC)：2026-11-09T04:10:24.594851+00:00
- commit：`d6ea2ed4e28a74c0c530f86101f477d9993109e0+dirty`
- 数据集：retrieval_zh.jsonl（split=dev，55 条，label_source=synthetic，sha256 `5ffd852d48c9`）
- Embedding：mock / mock-hash-embedding-v1（revision `None`，dim 1024）；QA 生成器：mock-extractive
- Reranker：rule=lexical-overlap-v1(rule)
- 配置：`{"chunk_size": 500, "chunk_overlap": 80, "rrf_k": 60, "rerank_fusion": "rrf", "rerank_fusion_weight": 0.5, "retrieval_candidates": 50, "rerank_candidates": 20, "qa_evidence": "auto", "qa_min_evidence": 0.25, "qa_min_rerank_score": 0.02, "qa_require_anchor_terms": true, "hnsw_iterative_scan": "relaxed_order", "hnsw_ef_search": 100, "top_k": 10}`

> ⚠️ mock embedding = feature-hashed character/word unigrams: it measures lexical overlap only and has NO semantic understanding; vector/hybrid numbers here say nothing about real embedding quality.

| 模式 | Recall@5 | MRR@10 | nDCG@10 | P50 ms | P95 ms | 失败率 | ACL 泄露块数 |
|---|---|---|---|---|---|---|---|
| vector | 0.888 | 0.855 | 0.872 | 4.45 | 5.25 | 0.0 | 0 |
| keyword | 0.934 | 0.836 | 0.870 | 1.92 | 2.56 | 0.0 | 0 |
| hybrid | 0.946 | 0.859 | 0.876 | 5.27 | 6.29 | 0.0 | 0 |
| hybrid_rerank:rule | 0.957 | 0.878 | 0.896 | 8.09 | 9.4 | 0.0 | 0 |

按类别 Recall@5（无答案/无权限类无 gold，不计）：

| 模式 | error_code | exact_keyword | multi_doc | no_answer | no_permission | paraphrase |
|---|---|---|---|---|---|---|
| vector | 0.833 | 0.950 | 0.833 | - | - | 0.906 |
| keyword | 0.967 | 0.950 | 1.000 | - | - | 0.875 |
| hybrid | 0.867 | 0.950 | 1.000 | - | - | 0.969 |
| hybrid_rerank:rule | 0.967 | 0.950 | 1.000 | - | - | 0.938 |

QA（hybrid_rerank:rule，mock-extractive，profile=current，证据信号 auto，词面阈值 0.25 / 重排阈值 0.02，anchor=True）：
- 无答案/无权限问题正确拒答率：1.000
- 可回答问题的作答率：1.000
- 引用 ACL 泄露：0
- QA 延迟 P50/P95：8.88 / 10.58 ms
