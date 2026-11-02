# 独立测试集（held-out）

- `retrieval_zh_heldout.jsonl`：37 条问题，于 2026-11-02 12:4x（香港时间）在 **Stage 2.5 的任何调参之前**写好并单独提交冻结。
- 冻结校验：`sha256sum -c evals/heldout/FROZEN.sha256`。评测脚本每次运行都会校验哈希，不一致就拒绝运行。
- 规则：**只用于最终报告**。阈值、RRF、重排等参数只能在 dev 集（`evals/datasets/retrieval_zh.jsonl`）上调。如果确实发现标注错误，只能记录在 `evals/LABEL_CHANGES.md` 中（写明理由，并同时报告修改前和修改后的分数），不能静默修改。
- 类别：paraphrase 16 条、exact_keyword / error_code 6 条、multi_doc 5 条、no_answer 6 条（其中含 3 条与语料主题相近的"近邻无答案"问题）、no_permission 4 条。
- 标注来源：合成数据，未经人工复核。
