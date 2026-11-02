# 阶段 3 真实 Qwen 测试方案（未执行，等待逐轮授权）

> 本阶段**没有发起任何付费调用**。所有 Agent 运行、评测、E2E 均为 Mock（`OPSPILOT_AGENT_LLM_PROVIDER=mock`，`paid_max_calls=0`）。
> 下面两轮测试必须**分别**获得授权后才能执行；任一轮结束后停止并汇报，不自动进入下一轮。

## 1. 模型与官方价格
| 模型 | 输入 | 输出（非思考） | 来源 | 核对日期 |
|---|---|---|---|---|
| qwen-plus（默认） | ¥0.8 / 百万 tokens | ¥2.0 / 百万 tokens | https://help.aliyun.com/zh/model-studio/model-pricing （0<输入≤128K 档，华北2，原价，不计免费额度/优惠） | 2026-11-02 |
| qwen-flash（备选，更便宜） | ¥0.15 / 百万 | ¥1.5 / 百万 | 同上 | 2026-11-02 |

价格同时记录在 `configs/model_prices.json`，网关按它计费并做预算预留。执行前需再次核对官网价格。思考模式输出价为 ¥8/百万，本方案**不开启**思考模式。

## 2. Token 估算依据
Mock 评测中统计（字符估算）每次运行约 **3,460 输入 / 334 输出 tokens**（含分类、计划、ReAct 决策、生成，最多 `max_llm_calls=8` 次调用；`max_tokens` 被网关钳制在 600）。真实模型输出可能更长，下面给出“预期”和“硬上限”两个数字。

## 3. 第一轮：冒烟测试（申请上限 ≤ ¥5，实际预算设 ¥1）
- 场景：5 个（a02 直接问答、a12 多工具、a17 无答案拒答、a32 提示注入、a39 审批中断+批准恢复）
- 请求数：预期约 15–25 次 LLM 调用；硬上限 `OPSPILOT_PAID_MAX_CALLS=40`
- Token：预期 ~17k 输入 / ~1.7k 输出；
- 费用：预期 **≈ ¥0.02**；硬上限（40 次 × 4k 输入 + 600 输出）≈ **¥0.18**；网关预算 `OPSPILOT_PAID_BUDGET_CNY=1`
- 命令（授权后才执行；Key 只从 OpsPilot 自己的 `.env` 读取）：
  ```bash
  OPSPILOT_AGENT_LLM_PROVIDER=qwen OPSPILOT_ALLOW_PAID_API=true \
  OPSPILOT_PAID_APPROVAL_ID=<本轮授权号> OPSPILOT_PAID_BUDGET_CNY=1 OPSPILOT_PAID_MAX_CALLS=40 \
  OPSPILOT_PAID_MAX_CONCURRENCY=1 \
  uv run python ../evals/run_agent_eval.py --provider qwen --tag qwen_smoke --only a02,a12,a17,a32,a39
  ```

## 4. 第二轮：完整评测（申请上限 ≤ ¥30，实际预算设 ¥3）
- 场景：41 个（约 45 次运行，含多轮/审批恢复）
- 请求数：预期约 130–200 次；硬上限 `OPSPILOT_PAID_MAX_CALLS=300`
- Token：预期 ~156k 输入 / ~15k 输出
- 费用：预期 **≈ ¥0.16**（3 倍余量约 ¥0.5）；硬上限（300 × 4k + 600）≈ **¥1.32**；网关预算 `OPSPILOT_PAID_BUDGET_CNY=3`
- 可选：`OPSPILOT_AGENT_LLM_JUDGE=true` 会让证据节点多一次调用（约 +40 次、+¥0.05），默认关闭。

## 5. 守卫（已实现并有测试）
`allow_paid_api` 开关 + 每轮授权号 + 预算预留 + 调用次数上限 + 输出 token 钳制 + 并发信号量 + 超时（`max_retries=0`，不自动重试计费）+ 调用账本。超预算/超次数在**发请求前**拒绝。

## 6. 产出
`evals/results/*_qwen_*.json`（标注真实模型、实际 tokens 与费用），与 Mock 结果对照写入 STAGE3_REPORT 附录。
