# Agent 评测集

- `agent_eval_v1.jsonl`：阶段 3/4/5 的 45 条回归。
- `agent_eval_v2.jsonl`：阶段 6 扩展至 ≥100 条，含 `taxonomy` / `split` / `label_source`。
- `agent_eval_v2_final_test.jsonl` + `FROZEN_v2.sha256`：最终测试集冻结哈希（调参前）。

```bash
uv run python evals/run_agent_eval.py --prepare --db-url "$OPSPILOT_TEST_DATABASE_URL" \
  --dataset evals/agent/agent_eval_v2.jsonl --split final_test --verify-hash --tag stage6_v2_final_mock
```

Mock 结果不能作为真实模型能力证据。付费对照见 `docs/STAGE6_QWEN_COMPARE_PLAN.md`。
