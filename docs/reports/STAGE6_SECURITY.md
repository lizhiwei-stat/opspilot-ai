# 阶段 6 安全测试报告

自动化：`tests/stage6/test_security_matrix.py` + `test_observability_security.py` + 既有 unit/mcp（JWT、注入、MCP 令牌、审批）。2026-11-09 box：`pytest tests/stage6` → **12 passed**；全量 **269 passed, 1 skipped**。

| # | 项 | 结果 |
|---|---|---|
| 1 | JWT 过期 | 过期 token → 401（SecretStr 正确取出） |
| 2 | RBAC | alice 访问 admin 路由 → 403 |
| 3 | 知识库隔离 | alice 看不到 `hr-confidential`；检索候选不含 hr |
| 4 | Prompt Injection | 不泄露 Admin 密码 / sk-；仍无 admin API |
| 5 | MCP 权限 | 既有 mcp 测试 + 调用者令牌（阶段 4） |
| 6 | 审批绕过 | 普通用户 POST approval → 403 |
| 7 | 上传安全 | `.exe` 不会被标为已索引 |
| 8 | 输入校验 | 登录/建库/密码规则既有测试 |
| 9 | 密钥管理 | settings 仅 `api_key_configured` bool；日志 redact 测试通过 |
| 10 | 日志脱敏 | `register_secret` + 形状匹配；观测接口输出无密钥 |
| 11 | 依赖扫描 | `pip-audit`：无已知漏洞（本项目本地包跳过）；`npm audit`：10 个 moderate（eslint 工具链，非运行时） |
| 12 | 资源耗尽 | Agent `GraphRecursionError` / 超时故障注入场景在评测中覆盖 |

KB 删除：错误 slug → 409；非所有者 → 403；正确确认后删除。
