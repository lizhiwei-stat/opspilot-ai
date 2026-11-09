# 阶段 6：真实 Qwen 对照评测计划（**未执行，待批准**）

> 本文件只是预算与方案。在 Teddy 明确批准之前，**不得**设置 `OPSPILOT_ALLOW_PAID_API=true`，不得调用 DashScope / Qwen。

## 目的
在冻结的 `agent_eval_v2` **final_test** 子集上，对比 Mock 规则智能体与真实 `qwen-plus` 的任务成功率、工具选择、拒答、时延与费用。Mock 结果不能代表模型推理能力。

## 模型与官价（查阅日：2026-11-09）
- **模型**：`qwen-plus`（与当前配置默认一致；等价快照以控制台为准，计划按非思考模式计价）
- **官方来源**：[阿里云 Model Studio 模型价格](https://help.aliyun.com/en/model-studio/model-pricing)（China / Beijing，`qwen-plus`，0&lt;输入≤128K）
- **单价（元 / 百万 tokens）**：
  - 输入：**¥0.8**
  - 输出（非思考）：**¥2**
  - 输出（思考）：¥8（本计划**关闭思考模式**，按 ¥2 计）

## 范围与请求量
| 项 | 值 |
|---|---|
| 数据集 | `evals/agent/agent_eval_v2_final_test.jsonl`（冻结 SHA 见 `FROZEN_v2.sha256`） |
| 场景数 | 31（final_test） |
| 每场景估计模型调用 | 平均 2 次（分类 / 工具规划 / 生成；含重试上限） |
| 总请求上限硬封顶 | **80** 次 chat 调用（`OPSPILOT_PAID_MAX_CALLS=80`） |
| 估计输入 tokens / 次 | 1 500（检索摘要 + 历史，偏保守） |
| 估计输出 tokens / 次 | 400 |
| 估计总 tokens | 80 × (1500+400) = **152 000** |

## 费用估算
- 输入：152k × 1500/1900 ≈ 120 000 tok → 0.12 × ¥0.8 = **¥0.096**
- 输出：152k × 400/1900 ≈ 32 000 tok → 0.032 × ¥2 = **¥0.064**
- **点估计合计 ≈ ¥0.16**
- **申请预算上限：¥5.00**（含重试、工具多轮、价格浮动；网关 `OPSPILOT_PAID_BUDGET_CNY=5`）
- 单任务成本上限：¥0.20（超出记失败并停止该场景）

## 护栏（执行时才设置，本阶段不设置）
```bash
# 仅在批准后由人工导出到一次性 shell；不要写入仓库
export OPSPILOT_ALLOW_PAID_API=true
export OPSPILOT_PAID_APPROVAL_ID='stage6-qwen-compare-<date>'
export OPSPILOT_PAID_BUDGET_CNY=5
export OPSPILOT_PAID_MAX_CALLS=80
export OPSPILOT_LLM_PROVIDER=openai_compatible
export OPSPILOT_AGENT_LLM_PROVIDER=openai_compatible
export OPSPILOT_LLM_MODEL=qwen-plus
# OPSPILOT_LLM_API_KEY 已在本地 .env，不打印、不提交
```
命令（批准后）：
```bash
uv run python evals/run_agent_eval.py --prepare --provider qwen \
  --dataset evals/agent/agent_eval_v2_final_test.jsonl --split final_test \
  --verify-hash --tag stage6_qwen_final
```
只在 `*_test` / `*_eval` 库上 `--prepare`。

## 对照指标
与 Mock baseline 同一套：Task Success、Tool Selection Acc、Tool Exec Success、Abstention Acc、Answer Groundedness、P50/P95/P99、tokens、¥/task；失败分类与根因。

## 批准清单
- [ ] 同意模型 `qwen-plus` 非思考模式
- [ ] 同意预算 **¥5**、硬封顶 **80** 次调用
- [ ] 同意仅跑 final_test 31 条
- [ ] 同意在一次性 eval 库执行，不改主库业务数据
