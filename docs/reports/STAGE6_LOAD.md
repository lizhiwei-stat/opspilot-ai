# 阶段 6 负载与可靠性报告（Mock）

环境：box 一次性库 `opspilot_s6dev_test`，uvicorn `127.0.0.1:18020`，LLM/Embedding/Agent = mock，2026-11-09（Asia/Hong_Kong）。
脚本：`scripts/load_reliability_mock.py` → `evals/results/20261109T034743Z_load_reliability_mock.json`。

| 检查 | n | 成功率 | p50 / p95 / p99 (ms) | 说明 |
|---|---:|---:|---|---|
| 单用户仪表盘 | 20 | 100% | 5.4 / 6.7 / 58.0 | GET `/dashboard/summary` |
| 并发 `/health/ready` | 40（并发 8） | 100% | 14.0 / 24.9 / 28.4 | |
| RAG hybrid 检索 | 15 | 100% | 8.8 / 12.9 / 378.5 | 冷启动一次偏高 |
| Agent 同步运行 | 3 | 100% | 73.4 / 2704.7 / 2704.7 | 含 MCP stdio 拉起，首包慢 |
| SSE 断线 | 1 | 100% | 16.7 | 读部分字节后断开 |

## 未在本 harness 强制执行（如实）
- MCP Server 硬杀、PostgreSQL 短暂不可用：见 Agent 评测故障注入 + 运维手册演示；不对共享主库做破坏。
- 连接池耗尽：未对共享库加压到失败。

瓶颈：Agent 首次 MCP stdio 子进程冷启动可达 ~2.7s；稳态仪表盘与检索在数十毫秒级。
