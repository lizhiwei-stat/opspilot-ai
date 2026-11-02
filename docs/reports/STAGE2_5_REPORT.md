# Stage 2.5 报告：真实免费本地 Embedding、混合检索评测与修复

日期：2026-11-02 · 验证环境：WSL2 Ubuntu-24.04（16 核 / 15 GB / 仅 CPU）+ Docker Compose（PostgreSQL 16 + pgvector 0.8.7）· **全程零付费调用**
详细数字与分析：[STAGE2_5_EVAL.md](STAGE2_5_EVAL.md) · 自动生成的全部表格：[STAGE2_5_TABLES.md](STAGE2_5_TABLES.md) · Notebook：[notebooks/real_embedding_hybrid.ipynb](../../notebooks/real_embedding_hybrid.ipynb) · 付费对照方案（未执行）：[STAGE2_5_PAID_EMBED_PLAN.md](../STAGE2_5_PAID_EMBED_PLAN.md) · 下一阶段设计：[STAGE3_DESIGN.md](../STAGE3_DESIGN.md)

## 1. 完成内容

| 要求 | 结果 |
|---|---|
| 真实免费本地 Embedding | bge-small-zh-v1.5（512 维，默认本地模型）、bge-m3（1024 维）在 CPU 上运行，固定 HF revision；每个模型独立索引（`embedding_models` + `chunk_embeddings` + 部分 HNSW），mock 向量保留 |
| 四种检索模式真实评测 | vector / keyword / hybrid(RRF) / hybrid+reranker（**rule**、**mock**、**真实 cross-encoder** bge-reranker-base 三种标签） |
| 指标 | Recall@5、MRR@10、nDCG@10、P50/P95、失败率、正确拒答率、作答率、ACL 隔离正确率，按题型细分 |
| held-out ≥30 | 37 条，调参前冻结（SHA-256），唯一标注修改 LC-001 有记录且发生在首次打分前 |
| Mock 基线并排 | 汇总表 + 前端矩阵（截图 `docs/assets/stage25-evals-matrix.png`） |
| (a) 11 个改写误拒 | dev 上全部修复（阈值 0.5→0.25，dev 调参，规则事先声明） |
| (b) q045/q048 误答 | anchor 检查修复 |
| (c) reranker multi_doc 下降 | 根因定位 + RRF 融合修复，multi_doc .881→1.000；修复前后文件保留 |
| (d) iterative scan | 10 万行实验：off 返回 0.37 行/查询，relaxed_order 召回 .72；应用启用 relaxed_order |
| (e) 真实 token 数 | `char_count` 改名 + 每模型 tokenizer 真实 token 数 |
| (f) 超越正则的注入防御 | 输出策略层（链接白名单、密钥形状、动作声明、不可信标记）+ "服从注入的假 LLM" 测试；修复 1 个真实漏洞 |
| (g) 边界情况 | 降级、维度不匹配、索引隔离、空/超长查询等测试 |
| 逐条错误分析 | 每条结果带 failure_category；前端可查看 |
| 统一模型调用层 | `ModelGateway`：Mock / 本地 / DashScope Embedding / Qwen Chat；调用次数、token、成本、预算上限、硬停止、账本、脱敏 |
| Stage 3 方案 | 仅设计文档，未写 Agent 代码 |

另外发现并修复：cross-encoder 双 sigmoid（分数全部挤在 0.5–0.73）。

## 2. 核心结果（摘要）

| 配置 | dev R@5 / MRR | held-out R@5 / MRR | P50 |
|---|---|---|---|
| keyword | .934 / .836 | .963 / .867 | 2.4 ms |
| bge-small vector | .891 / .810 | .889 / .796 | 12 ms |
| bge-m3 vector | .950 / .922 | .926 / .883 | 60 ms |
| bge-small hybrid | .954 / .893 | .926 / .869 | 12 ms |
| bge-small hybrid+rule（默认） | .988 / .909 | .963 / .901 | 16 ms |
| bge-small hybrid+cross-encoder | **1.000 / .965** | **1.000 / .938** | ~1.6–2.0 s |
| mock hybrid+rule（基线） | .957 / .878 | 1.000 / .914 | 10 ms |

ACL 隔离 1.000（0 泄漏）、失败率 0：所有配置。
问答：dev 正确拒答/作答 1.000/1.000；**held-out 0.600/0.963（rule）、0.500/1.000（CE）**，低于 Stage 2 基线的拒答率 0.700 —— 阈值在 dev 上过拟合，见 §4。

## 3. 验证状态

### 已实际验证（WSL2，2026-11-02）
- `pytest`：**145 passed**（单元 + 连真实 PostgreSQL `opspilot_test` 库的集成测试 + 3 个 `local_model` 测试，使用缓存的真实模型，`HF_HUB_OFFLINE=1`）；`-m local_model` 单独 3 passed
- `ruff check` / `ruff format --check`：通过
- 前端：容器内 `npm test` 11 passed、`npm run build` 通过（盒子沙箱同样 11 passed）
- 整套 Compose 重新构建并保持运行（backend healthy，alembic head = 0003）；Windows 无头 Edge E2E（登录 ×2 + RAG 全流程 + 新的 Mock vs 真实矩阵检查）通过
- 全部评测（mock/bge-small/bge-m3 × dev/held-out × 各模式 × 修复前后）、本地模型基准、iterative scan 实验、阈值扫描：真实运行，结果 JSON 已提交
- Notebook 在 WSL 上离线执行，输出保留

### 仅静态检查 / 单元测试（未在真实服务上运行）
- Qwen chat 付费路径：只用假 provider 测试守卫（`tests/unit/test_gateway.py` 11 项），**从未发起真实请求**
- 付费实验方案中的价格：来自阿里云官方文档页（2026-11-02），未经实际账单核对
- Stage 3 设计中的 LangGraph API：依据官方文档与 PyPI，未写代码、未运行

### 尚未验证
- bge-m3 + cross-encoder 组合（CPU 时间成本，未跑）
- 大语料（>1 万 chunk）下的延迟与召回；当前语料仅 42 个 chunk
- 真实 LLM 生成答案的质量（仍为 mock 抽取式回答器）
- GitHub Actions 未在 GitHub 上运行（未推送）

## 4. 风险

1. **拒答阈值过拟合 dev**：held-out 上近话题无答案问题（h029/h033/h036 等）cross-encoder 也给高分，阈值无法区分。看到 held-out 后未再调参。→ Stage 3 引入 LLM 可回答性判断 + 引用校验，并重新在 held-out 上评估。
2. **数据规模小**：42 chunk / 92 题，置信区间宽；held-out 上 mock vector 高于 bge-small vector，说明结论对数据敏感。
3. **cross-encoder 延迟**：CPU ~2 s/查询，不能作为默认在线路径。
4. **E2E 偶发 401**：5 次完整 E2E 中有 2 次在浏览器流程里出现一次 401（登录后立即调用），重跑通过；同样的 API 序列用 curl 连续 40 次均 200，根因未定位（疑似前端会话/请求时序），已记录待查。
5. **torch 依赖体积**：`local-embed` 组约 1 GB；`uv sync --all-groups` 会把它装上。
6. 前端页头版本仍显示 `v0.2.0 · Stage 2`（未改）。

## 5. 下一阶段（Stage 3）验收标准
见 [STAGE3_DESIGN §11](../STAGE3_DESIGN.md#11-stage-3-验收标准)，要点：≥30 个 Mock 场景 CI 通过；获批后 ≥30 个真实 Qwen 场景，总费用 ≤ ¥30 且账本可审计；工单 100% 经人工审批、伪造 resume 测试失败；步数/调用/费用上限安全终止；checkpoint 恢复不重复建单；Agent 全路径 ACL 不泄漏；`langgraph` schema 不受 Alembic 管理；held-out 拒答率较本阶段提升。

## 6. 复现
见 [STAGE2_5_EVAL §12](STAGE2_5_EVAL.md#12-复现命令wsl)。测试：

```bash
uv sync --group dev --group local-embed
HF_HUB_OFFLINE=1 uv run --group local-embed pytest -q        # 需要 compose 中的 PostgreSQL
uv run ruff check . && uv run ruff format --check .
docker compose --env-file .env -f infra/docker-compose.yml exec frontend npm test
powershell -File scripts\e2e_windows.ps1 -Shots <截图目录>     # Windows 侧
uv run --group local-embed --group notebook jupyter nbconvert --to notebook --execute --inplace notebooks/real_embedding_hybrid.ipynb
```

## 7. 付费对照实验（stage-2.5.1）
经 Teddy 批准（approval id `teddy-2026-11-02-t63s266`，2026-11-02 13:49 HKT），在 WSL 上运行百炼 text-embedding-v4（1024 维，独立索引）dev + held-out 评测：473 次调用、**9,272 输入 tokens（API usage）、实际费用 ¥0.004636**，每次运行上限 ¥1 未触发；Key 未落盘，泄漏检查 0 命中。结果：向量 MRR 高（dev .924）但 Recall@5 偏低（.872，multi_doc .691），混合 + 重排后与本地模型持平，延迟高 50 倍以上——默认仍用本地 bge-small。详见 [STAGE2_5_EVAL §13](STAGE2_5_EVAL.md#13-付费对照实验百炼-text-embedding-v4stage-251已获批准并执行)。
