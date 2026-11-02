# Stage 2.5 付费 Embedding 对照实验方案（待批准，**尚未执行**）

> 状态更新（2026-11-02 13:57 HKT）：**已获批准并执行**（approval id `teddy-2026-11-02-t63s266`），实际 9,272 tokens / ¥0.004636，结果见 [STAGE2_5_EVAL §13](reports/STAGE2_5_EVAL.md)。以下为原方案。
>
> 原状态：**仅方案**。本仓库截至 stage-2.5 没有发起过任何付费 API 调用；`OPSPILOT_ALLOW_PAID_API` 默认 `false`，盒子/WSL 上的 Key 未被读取、打印或写入文件。执行前需要 Teddy 单独书面批准，并给出 approval id。

## 1. 目的

在**同一语料、同一 55 条 dev + 37 条冻结 held-out** 上，比较：

| 方案 | 说明 | 状态 |
|---|---|---|
| keyword | PostgreSQL 全文检索（jieba 分词） | 已有结果（免费） |
| 本地 bge-small-zh-v1.5（512 维） | CPU，`7999e1d3` | 已有结果（免费） |
| 本地 bge-m3（1024 维） | CPU，`5617a9f6` | 已有结果（免费） |
| **百炼 text-embedding-v4（1024 维）** | 付费 API | **待批准** |
| 以上各自的 hybrid(RRF) / hybrid+rule / hybrid+cross-encoder | 复用已有 reranker | 付费部分待批准 |

## 2. 模型与官方价格（2026-11-02 核对）

- 模型：`text-embedding-v4`（阿里云百炼，OpenAI 兼容接口 `https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings`）
- 维度：支持 2048/1536/1024(默认)/768/512/256/128/64；本实验固定 **1024**
- 单批最多 10 条（与 `OPSPILOT_EMBEDDING_BATCH_SIZE=10` 一致）
- **价格：0.0005 元 / 千输入 Token（= 0.5 元 / 百万 Token），中国内地（北京）地域，原价**；不计免费额度、不计促销
- 来源（2026-11-02 访问）：
  - https://help.aliyun.com/zh/model-studio/text-embedding-v4
  - https://help.aliyun.com/zh/model-studio/embedding
  - https://help.aliyun.com/zh/model-studio/model-pricing
- 价格已写入 `configs/model_prices.json`（`verified: 2026-11-02`），预算守卫按此计费；执行前需再核对一次。

## 3. Token 用量估算（方法 + 结果）

方法：实际解析并切分语料（`chunk_size=500, overlap=80`，与评测一致），统计字符数；Qwen 系分词器对中文约 0.6–1.0 token/字，**按 1 token/字符的保守上界**估算（与网关 `ModelGateway` 的事前估算规则一致）。参照：本地真实分词器计数 bge-small 4,757 tokens、bge-m3 3,879 tokens（`evals/results/bench_local_models.json`），都低于字符数，说明上界保守。

| 项 | 数量 | 字符数（≈ token 上界） |
|---|---|---|
| 语料 chunk | 42 | 6,323 |
| dev 查询 | 55 | ≈ 880 |
| held-out 查询 | 37 | ≈ 590 |
| 查询合计 | 92 | 1,472 |
| **一次完整实验（建索引 + 两套查询）** | | **≈ 7,795** |

计划执行 **2 次**完整实验（第 2 次用于确认延迟/稳定性；索引幂等，第 2 次只会重新嵌入查询），再加 20% 余量（失败重试、QA 路径的查询嵌入）：

- 上界 token ≈ (6,323 + 1,472×2(检索+QA)) × 1 + 1,472×2 × 1(第 2 次) ≈ 12,211 → ×1.2 ≈ **14,700 tokens**
- 费用上界 ≈ 14,700 / 1,000 × 0.0005 = **¥0.0074（不到 1 分钱）**

## 4. 预算与守卫

- 用户总预算：**¥10**（本实验）。单次运行硬上限：`OPSPILOT_PAID_BUDGET_CNY=1.00`（远高于估算，又远低于 ¥10；不会自动提高）。
- 守卫（`backend/src/opspilot/llm/gateway.py`，单元测试 `tests/unit/test_gateway.py` 11 项，全部用假付费 provider，不联网）：
  1. `OPSPILOT_ALLOW_PAID_API=true` 才允许；默认关闭
  2. `OPSPILOT_PAID_APPROVAL_ID` 必填，写入每条账本
  3. 价格表缺模型 → 拒绝
  4. 调用**前**估算（1 token/字），超出剩余预算 → `BudgetExceeded`，不发请求
  5. 调用**后**按 API 返回的 `usage.prompt_tokens` 记账；缺 usage 时按估算记
  6. 每次调用（成功/失败）写入 JSONL 账本 `OPSPILOT_MODEL_LEDGER_PATH`；累计 ≥ 上限后拒绝所有后续调用
  7. Key 只从环境变量读取；日志/账本/报告经 `core/redact.py` 脱敏（形状 + 已注册值），有测试
- 评测脚本另有一道人工确认：`--embedding dashscope` 必须同时带 `--i-understand-this-costs-money`。

## 5. 索引隔离

- `embedding_models` 中注册为 `text-embedding-v4`（provider=openai_compatible, dim=1024），获得独立 id 和独立部分 HNSW 索引 `ix_chunk_emb_hnsw_m{id}`；不会与 mock(1024 维，不同 id)、bge-small(512)、bge-m3(1024) 混用。
- 若有人以同名不同维度运行（如 dims=512），注册即抛 `EmbeddingDimensionMismatch`（409）。本方案只跑 1024 维；如需对比其他维度，需另起模型名（当前未实现名字后缀，**尚未支持**）。
- 现有 mock / 本地向量不受影响；回滚方式：`scripts/reindex_embeddings.py` 不会删除其它模型，删除该模型向量需显式 `--recreate`。

## 6. 精确执行命令（批准后才运行，在 WSL）

```bash
cd ~/projects/opspilot-ai
# Key 由用户在当前 shell 注入（不写文件、不回显）；以下仅为变量名
export OPSPILOT_EMBEDDING_PROVIDER=openai_compatible
export OPSPILOT_EMBEDDING_MODEL=text-embedding-v4 OPSPILOT_EMBEDDING_DIM=1024 OPSPILOT_EMBEDDING_BATCH_SIZE=10
export OPSPILOT_ALLOW_PAID_API=true
export OPSPILOT_PAID_APPROVAL_ID=<Teddy 给出的批准编号>
export OPSPILOT_PAID_BUDGET_CNY=1.00
export OPSPILOT_MODEL_LEDGER_PATH=evals/results/paid_ledger_<approval>.jsonl
# OPSPILOT_LLM_API_KEY 由用户手动 export（read -s），不写入任何文件
for d in dev heldout; do
  uv run --group local-embed python evals/run_retrieval_eval.py --embedding dashscope \
     --i-understand-this-costs-money --dataset $d --rerankers rule,cross_encoder --tag dashscope_$d
done
```

产出：`evals/results/*_dashscope_{dev,heldout}.json/.md`、成本账本（只含 token/费用/延迟，不含 Key），并在报告中与本地/关键词结果并排。执行后立刻 `unset OPSPILOT_ALLOW_PAID_API OPSPILOT_LLM_API_KEY`。

## 7. 停止条件

- 任一调用 `BudgetExceeded` / 账本累计 ≥ ¥1 → 立即停止，不重试、不提额
- 出现 401/403/429 → 停止并报告，不切换 Key
- 实际费用 > 估算 10 倍 → 停止并报告
