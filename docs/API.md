# API 设计

Base URL：`http://localhost:8000`。交互式文档：`/docs`（Swagger）、`/openapi.json`。
认证：`Authorization: Bearer <JWT>`。所有响应带 `X-Request-ID`。

**统一错误格式**
```json
{"error": {"code": "unauthorized", "message": "Invalid email or password", "request_id": "6b2f…", "details": null}}
```
错误码：`validation_error`(422) `unauthorized`(401) `forbidden`(403) `not_found`(404) `conflict`(409) `dependency_unavailable`(503) `internal_error`(500)。

状态列：✅ 已实现并测试；🔜 计划阶段。

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

## 知识库与检索（阶段 2）✅
权限：读 = admin / public / owner / `kb_permissions` 授权；写 = admin / owner / `write|admin` 授权。无读权限一律 **404**（不暴露资源是否存在），有读无写 **403**。

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/v1/knowledge-bases` | 登录 | 只返回可读的知识库：`{id, slug, name, description, visibility, document_count, chunk_count, can_write}` |
| GET | `/api/v1/knowledge-bases/{kb_id}/documents` | 读 | 文档列表：`current_version, latest_status, latest_error, chunk_count` |
| POST | `/api/v1/knowledge-bases/{kb_id}/documents` | 写 | multipart：`file`（必填）、`title`（可选）。超过 `OPSPILOT_MAX_UPLOAD_MB` → **413**。其余情况 200，返回 `{status: indexed|duplicate|failed, message, chunk_count, document{…, versions[]}}`；`failed` 时 `document.latest_error = "[错误码] 中文说明"`，失败版本会保留下来供查看 |
| GET | `/api/v1/documents/{doc_id}` | 读 | 详情 + 全部版本（`index_status: indexing/indexed/failed/superseded`、sha256、embedding_model） |
| GET | `/api/v1/documents/{doc_id}/chunks?offset&limit` | 读 | 当前版本分块：`content, page_number, heading_path, char_start, char_end, search_tokens` |
| POST | `/api/v1/documents/{doc_id}/reindex` | 写 | 按当前 embedding 模型重新分块与向量化；失败 **422**，原索引保留 |
| DELETE | `/api/v1/documents/{doc_id}` | 写 | 软删除文档，硬删除分块：`{status: deleted, removed_chunks}` |
| POST | `/api/v1/search` | 登录 | `{query, mode: vector|keyword|hybrid|hybrid_rerank, top_k≤50, kb_ids?}` → `{tokens, candidates[{chunk_id, rank, score, content, document_name, version, kb_slug, page_number, heading_path, char_start, char_end, suspicious, scores{vector,keyword,rrf,rerank}, ranks{…}}], timings_ms, embedding_model, reranker}` |
| POST | `/api/v1/qa` | 登录 | `{question, top_k≤20, kb_ids?}` → `{answer, generator, is_mock, evidence_sufficient, evidence_score, threshold, citations[{index, chunk_id, document_name, kb_slug, version, page_number, locator, evidence, quote}], flagged_chunks, candidates, timings_ms}` |

阶段 2.5 补充（向后兼容，新增字段）：
- `/search` 响应新增 `degraded`（`null` 或原因字符串，如 `embedding_unavailable:<异常类型>`、`no_vectors_for_model`：embedding 不可用时自动降级为关键词检索；数据库错误不降级，直接 500）；`reranker` 标明类型（rule / mock / cross-encoder 模型名）；`embedding_model` 为实际使用的隔离索引名。
- `/qa` 新增 `refusal_reason`（`below_threshold`、`unmatched_anchor:<词>`、`policy_violation`、`no_valid_citation`）、`policy_violations`（`untrusted_*`、`link_not_allowlisted:<host>`、`secret_like_string`、`action_claim`，见 `rag/policy.py`）、`degraded`。
- `GET /api/v1/evals/runs` 的 `config` 含 `embedding_model / dataset_split / rerank_fusion / qa_profile`，前端据此画 Mock vs 真实并排矩阵。

上传解析错误码：`unsupported_type` `empty_file` `too_large` `content_mismatch` `binary_content` `decode_failed` `pdf_encrypted` `pdf_corrupt` `pdf_no_text` `no_chunks` `embedding_failed`。

🔜 知识库创建 / 授权管理接口（`POST /knowledge-bases`、`PUT/DELETE …/permissions/{user_id}`）放在阶段 5 管理界面，目前由种子脚本创建。

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
| GET | `/api/v1/evals/runs` | ✅ 阶段 2：评测运行列表（指标、配置、commit、mock 警告） |
| GET | `/api/v1/evals/runs/{id}` | ✅ 阶段 2：指标 + 逐条结果 |
| POST | `/api/v1/evals/runs` | 🔜 触发评测（admin）；目前通过 `evals/run_retrieval_eval.py` 离线运行 |

## MCP Server（阶段 4，独立进程）
工具：`search_knowledge`、`query_mock_logs`、`get_ticket`、`create_ticket_draft`。输入输出以 Pydantic 模型声明；调用方身份通过服务端令牌传入并做 ACL 校验；不提供任何直接创建正式工单的工具。
