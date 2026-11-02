# evals/

阶段 6 在此放置评测数据集与脚本。约定：

- 每条用例必须带 `label_source`：`human`（人工标注）或 `synthetic`（合成，需注明生成方式）；
- 数据集文件记录来源与 SHA-256，评测结果写入 `eval_runs` / `eval_case_results` 表；
- 不提交任何未经实际运行得到的指标。

阶段 1 尚无评测数据和脚本。
