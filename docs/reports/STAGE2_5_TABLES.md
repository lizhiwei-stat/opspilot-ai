# Stage 2.5 评测汇总表（由 evals/summarize_stage25.py 从 evals/results/*.json 生成）

## 检索：dev（55 条）

| Embedding | 模式 | Recall@5 | MRR@10 | nDCG@10 | P50 ms | P95 ms | 失败率 | ACL 隔离正确率 | multi_doc R@5 | paraphrase R@5 |
|---|---|---|---|---|---|---|---|---|---|---|
| mock (hash, 无语义) | vector | 0.888 | 0.855 | 0.872 | 6.01 | 7.11 | 0.0 | 1.000 | 0.833 | 0.906 |
| mock (hash, 无语义) | keyword | 0.934 | 0.836 | 0.870 | 2.4 | 3.5 | 0.0 | 1.000 | 1.000 | 0.875 |
| mock (hash, 无语义) | hybrid | 0.946 | 0.859 | 0.876 | 5.88 | 6.81 | 0.0 | 1.000 | 1.000 | 0.969 |
| mock (hash, 无语义) | hybrid_rerank:rule | 0.957 | 0.878 | 0.896 | 9.7 | 11.62 | 0.0 | 1.000 | 1.000 | 0.938 |
| mock (hash, 无语义) | hybrid_rerank:cross_encoder | 0.969 | 0.942 | 0.940 | 1958.08 | 2074.17 | 0.0 | 1.000 | 1.000 | 0.969 |
| bge-small-zh-v1.5 | vector | 0.891 | 0.810 | 0.843 | 12.13 | 14.1 | 0.0 | 1.000 | 0.881 | 0.969 |
| bge-small-zh-v1.5 | keyword | 0.934 | 0.836 | 0.870 | 2.43 | 3.33 | 0.0 | 1.000 | 1.000 | 0.875 |
| bge-small-zh-v1.5 | hybrid | 0.954 | 0.893 | 0.906 | 12.48 | 13.9 | 0.0 | 1.000 | 0.929 | 0.969 |
| bge-small-zh-v1.5 | hybrid_rerank:rule | 0.988 | 0.909 | 0.921 | 15.76 | 18.22 | 0.0 | 1.000 | 1.000 | 0.969 |
| bge-small-zh-v1.5 | hybrid_rerank:cross_encoder | 1.000 | 0.965 | 0.959 | 1966.66 | 2108.1 | 0.0 | 1.000 | 1.000 | 1.000 |
| bge-m3 | vector | 0.950 | 0.922 | 0.928 | 59.88 | 65.42 | 0.0 | 1.000 | 0.952 | 0.938 |
| bge-m3 | keyword | 0.934 | 0.836 | 0.870 | 2.43 | 3.09 | 0.0 | 1.000 | 1.000 | 0.875 |
| bge-m3 | hybrid | 0.957 | 0.929 | 0.931 | 61.07 | 68.19 | 0.0 | 1.000 | 0.952 | 0.938 |
| bge-m3 | hybrid_rerank:rule | 0.965 | 0.909 | 0.920 | 63.85 | 71.5 | 0.0 | 1.000 | 1.000 | 0.906 |

## 问答拒答：dev

| 配置 | 证据信号/阈值 | 正确拒答率 | 作答率 | 准确率 | 误答 | 误拒 | 来源文件 |
|---|---|---|---|---|---|---|---|
| Stage 2 基线（mock，lexical 0.5，无 anchor，replace） | lexical 0.5 | 0.833 | 0.744 | 0.764 | q045,q048 | q021,q022,q025,q026,q027,q029,q030,q031,q032,q033,q034 | 20261102T051843Z_before_mock_dev.json |
| Stage 2 基线（bge-small） | lexical 0.5 | 0.833 | 0.744 | 0.764 | q045,q048 | q021,q022,q025,q026,q027,q029,q030,q031,q032,q033,q034 | 20261102T051855Z_before_local_dev.json |
| 当前：mock + rule（lexical 0.25 + anchor） | lexical 0.25 | 1.000 | 1.000 | 1.000 | - | - | 20261102T051845Z_rule_mock_dev.json |
| 当前：bge-small + rule | lexical 0.25 | 1.000 | 1.000 | 1.000 | - | - | 20261102T051903Z_rule_local_dev.json |
| 当前：bge-m3 + rule | lexical 0.25 | 1.000 | 1.000 | 1.000 | - | - | 20261102T052623Z_m3_dev.json |
| 当前：bge-small + cross-encoder（rerank 0.02 + anchor） | reranker 0.02 | 1.000 | 1.000 | 1.000 | - | - | 20261102T050822Z_local_dev.json |
| 当前：mock + cross-encoder | reranker 0.02 | 1.000 | 1.000 | 1.000 | - | - | 20261102T051333Z_mock_dev.json |

## 检索：heldout（37 条冻结 held-out）

| Embedding | 模式 | Recall@5 | MRR@10 | nDCG@10 | P50 ms | P95 ms | 失败率 | ACL 隔离正确率 | multi_doc R@5 | paraphrase R@5 |
|---|---|---|---|---|---|---|---|---|---|---|
| mock (hash, 无语义) | vector | 0.944 | 0.836 | 0.859 | 5.95 | 7.38 | 0.0 | 1.000 | 0.900 | 0.938 |
| mock (hash, 无语义) | keyword | 0.963 | 0.867 | 0.892 | 2.42 | 3.6 | 0.0 | 1.000 | 0.800 | 1.000 |
| mock (hash, 无语义) | hybrid | 0.944 | 0.866 | 0.893 | 5.92 | 7.48 | 0.0 | 1.000 | 0.900 | 0.938 |
| mock (hash, 无语义) | hybrid_rerank:rule | 1.000 | 0.914 | 0.936 | 9.86 | 13.78 | 0.0 | 1.000 | 1.000 | 1.000 |
| mock (hash, 无语义) | hybrid_rerank:cross_encoder | 1.000 | 0.938 | 0.957 | 1991.45 | 2097.75 | 0.0 | 1.000 | 1.000 | 1.000 |
| bge-small-zh-v1.5 | vector | 0.889 | 0.796 | 0.819 | 11.93 | 13.83 | 0.0 | 1.000 | 0.600 | 0.938 |
| bge-small-zh-v1.5 | keyword | 0.963 | 0.867 | 0.892 | 2.62 | 3.5 | 0.0 | 1.000 | 0.800 | 1.000 |
| bge-small-zh-v1.5 | hybrid | 0.926 | 0.869 | 0.893 | 12.74 | 14.27 | 0.0 | 1.000 | 0.800 | 0.938 |
| bge-small-zh-v1.5 | hybrid_rerank:rule | 0.963 | 0.901 | 0.911 | 16.2 | 17.68 | 0.0 | 1.000 | 1.000 | 0.938 |
| bge-small-zh-v1.5 | hybrid_rerank:cross_encoder | 1.000 | 0.938 | 0.947 | 1645.23 | 2098.0 | 0.0 | 1.000 | 1.000 | 1.000 |
| bge-m3 | vector | 0.926 | 0.883 | 0.899 | 61.2 | 71.11 | 0.0 | 1.000 | 0.800 | 0.938 |
| bge-m3 | keyword | 0.963 | 0.867 | 0.892 | 2.62 | 3.38 | 0.0 | 1.000 | 0.800 | 1.000 |
| bge-m3 | hybrid | 0.926 | 0.926 | 0.927 | 61.39 | 72.01 | 0.0 | 1.000 | 0.800 | 0.938 |
| bge-m3 | hybrid_rerank:rule | 0.963 | 0.963 | 0.957 | 63.6 | 74.68 | 0.0 | 1.000 | 1.000 | 0.938 |

## 问答拒答：heldout

| 配置 | 证据信号/阈值 | 正确拒答率 | 作答率 | 准确率 | 误答 | 误拒 | 来源文件 |
|---|---|---|---|---|---|---|---|
| Stage 2 基线（mock，lexical 0.5，无 anchor，replace） | lexical 0.5 | 0.700 | 0.667 | 0.676 | h028,h029,h036 | h001,h005,h006,h009,h010,h012,h014,h016,h027 | 20261102T051848Z_before_mock_heldout.json |
| Stage 2 基线（bge-small） | lexical 0.5 | 0.700 | 0.667 | 0.676 | h028,h029,h036 | h001,h005,h006,h009,h010,h012,h014,h016,h027 | 20261102T051910Z_before_local_heldout.json |
| 当前：mock + rule（lexical 0.25 + anchor） | lexical 0.25 | 0.600 | 0.963 | 0.865 | h029,h033,h036,h037 | h006 | 20261102T051851Z_rule_mock_heldout.json |
| 当前：bge-small + rule | lexical 0.25 | 0.600 | 0.963 | 0.865 | h029,h033,h036,h037 | h006 | 20261102T051916Z_rule_local_heldout.json |
| 当前：bge-m3 + rule | lexical 0.25 | 0.600 | 0.926 | 0.838 | h029,h033,h036,h037 | h005,h006 | 20261102T052654Z_m3_heldout.json |
| 当前：bge-small + cross-encoder（rerank 0.02 + anchor） | reranker 0.02 | 0.500 | 1.000 | 0.865 | h029,h032,h033,h035,h036 | - | 20261102T051120Z_local_heldout.json |
| 当前：mock + cross-encoder | reranker 0.02 | 0.500 | 1.000 | 0.865 | h029,h032,h033,h035,h036 | - | 20261102T051630Z_mock_heldout.json |

## Reranker 融合方式（修复 c）前后

| 数据 | reranker | fusion | Recall@5 | MRR@10 | multi_doc R@5 | 文件 |
|---|---|---|---|---|---|---|
| dev/mock | rule | replace | 0.957 | 0.903 | 0.881 | 20261102T051843Z_before_mock_dev.json |
| dev/mock | rule | rrf | 0.957 | 0.878 | 1.000 | 20261102T051845Z_rule_mock_dev.json |
| dev/bge-small | rule | replace | 0.957 | 0.878 | 0.881 | 20261102T051855Z_before_local_dev.json |
| dev/bge-small | rule | rrf | 0.988 | 0.909 | 1.000 | 20261102T051903Z_rule_local_dev.json |
| dev/bge-small | cross_encoder | replace | 0.992 | 0.961 | 0.952 | 20261102T051923Z_ce_replace_local_dev.json |
| dev/bge-small | cross_encoder | rrf | 1.000 | 0.965 | 1.000 | 20261102T050822Z_local_dev.json |
| heldout/mock | rule | replace | 1.000 | 0.952 | 1.000 | 20261102T051848Z_before_mock_heldout.json |
| heldout/mock | rule | rrf | 1.000 | 0.914 | 1.000 | 20261102T051851Z_rule_mock_heldout.json |
| heldout/bge-small | rule | replace | 0.963 | 0.920 | 1.000 | 20261102T051910Z_before_local_heldout.json |
| heldout/bge-small | rule | rrf | 0.963 | 0.901 | 1.000 | 20261102T051916Z_rule_local_heldout.json |
| heldout/bge-small | cross_encoder | replace | 1.000 | 1.000 | 1.000 | 20261102T052046Z_ce_replace_local_heldout.json |
| heldout/bge-small | cross_encoder | rrf | 1.000 | 0.938 | 1.000 | 20261102T051120Z_local_heldout.json |

