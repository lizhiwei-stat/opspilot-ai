# API 设计

Base URL：`http://localhost:8000`。交互式文档：`/docs`（Swagger）、`/openapi.json`。
认证：`Authorization: Bearer <JWT>`。所有响应带 `X-Request-ID`。

**统一错误格式**
```json
{"error": {"code": "unauthorized", "message": "Invalid email or password", "request_id": "6b2f…", "details": null}}
```
错误码：`validation_error`(422) `unauthorized`(401) `forbidden`(403) `not_found`(404) `conflict`(409) `dependency_unavailable`(503) `internal_error`(500)。

状态列：✅ 阶段 1 已实现并测试；🔜 计划阶段。

## 系统 / 健康
| 方法 | 路径 | 权限 | 说明 | 状态 |
|---|---|---|---|---|
| GET | `/health/live` | 公开 | 存活探针，不访问依赖 | ✅ |
| GET | `/health/ready` | 公开 | DB、pgvector、迁移版本、LLM、Embedding；全部通过 200，否则 503 且返回每项详情 | ✅ |
| GET | `/api/v1/system/version` | 公开 | 版本、阶段、Provider 与模型 | ✅ |
| GET | `/api/v1/system/admin-ping` | admin | 角色校验探针 | ✅ |

## 认证
| 方法 | 路径 | 权限 | 说明 | 状态 |
|---|---|---|---|---|
| POST | `/api/v1/auth/login` | 公开 | `{email,password}` → `{access_token, token_type, expires_in, user}`；邮箱不区分大小写；错误信息统一，不泄露账号是否存在 | ✅ |
| GET | `/api/v1/auth/me` | 登录 | 当前用户 | ✅ |
| GET/POST/PATCH | `/api/v1/users` | admin | 用户管理 | 🔜 5 |

## 知识库（阶段 2）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/v1/knowledge-bases` | 列出（按 ACL）/ 创建（admin） |
| PUT/DELETE | `/api/v1/knowledge-bases/{kb_id}/permissions/{user_id}` | 授权 / 撤销 |
| POST | `/api/v1/knowledge-bases/{kb_id}/documents` | multipart 上传 PDF/MD/TXT；同名即新版本 |
| GET | `/api/v1/knowledge-bases/{kb_id}/documents` | 文档列表 + 索引状态 |
| GET | `/api/v1/documents/{doc_id}` · `/versions` · `/chunks` | 详情、版本、分块 |
| DELETE | `/api/v1/documents/{doc_id}` | 软删除，分块失效 |
| POST | `/api/v1/search` | `{query, kb_ids?, mode: basic|hybrid, top_k, rerank?}` → 带引用位置的片段 |

## Agent（阶段 3–4）
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/agent/runs` | 发起运行；`Accept: text/event-stream` 时流式推送节点事件 |
| GET | `/api/v1/agent/runs` · `/{run_id}` | 运行列表 / 详情（含 steps、tool_calls、引用） |
| GET | `/api/v1/agent/runs/{run_id}/events` | SSE 轨迹回放 |
| POST | `/api/v1/agent/runs/{run_id}/resume` | 从 checkpoint 恢复（审批后或中断后） |

## 工单与审批（阶段 4）
| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/v1/ticket-drafts?status=pending_approval` | admin | 待审列表 |
| GET | `/api/v1/ticket-drafts/{id}` | 创建者/admin | 草稿 + 证据 |
| POST | `/api/v1/ticket-drafts/{id}/approve` · `/reject` | admin | 写 `approvals`；批准后服务端校验哈希并创建工单，恢复 Agent |
| GET | `/api/v1/tickets` · `/{id}` | 登录 | 工单列表 / 详情 |
| PATCH | `/api/v1/tickets/{id}` | admin | 状态流转（修改类 Agent 操作同样需草稿 + 审批） |

## 评测与看板（阶段 5–6）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/dashboard/summary` | 工单、运行、工具成功率等真实聚合 |
| GET/POST | `/api/v1/evals/runs` | 列表 / 触发评测（admin） |
| GET | `/api/v1/evals/runs/{id}` | 指标 + 逐条结果 |

## MCP Server（阶段 4，独立进程）
工具：`search_knowledge`、`query_mock_logs`、`get_ticket`、`create_ticket_draft`。输入输出以 Pydantic 模型声明；调用方身份通过服务端令牌传入并做 ACL 校验；不提供任何直接创建正式工单的工具。
