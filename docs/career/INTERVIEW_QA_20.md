# 20 道项目面试题与参考答案

## Q1. 为什么用 LangGraph 而不是纯手写状态机？
**参考答案：** 需要 interrupt/检查点生态；见 agent/graph.py 与 ADR-003。

## Q2. PostgresSaver 存在哪？重启会怎样？
**参考答案：** Postgres；审批中的 run 可 resume，E2E/烟雾测验证过。

## Q3. MCP 和 Function Calling 的安全差异？
**参考答案：** 子进程隔离与调用者令牌；见 mcp/runtime.py。

## Q4. 如何防止模型直接建正式工单？
**参考答案：** 只能 create_ticket_draft；正式单经审批后 Ticket MCP。

## Q5. KB ACL 如何测？
**参考答案：** alice 列表不含 hr-confidential；检索候选校验；stage6 安全测。

## Q6. RRF 带来了什么？
**参考答案：** Mock 55 条上 hybrid R@5 0.946 > vector 0.888。

## Q7. 为何默认不用 cross-encoder？
**参考答案：** R@5 略升但 P95~2s，在线默认 rule/hybrid。

## Q8. SSE 推的是什么？
**参考答案：** 节点/工具/证据事件；Last-Event-ID 续传；非 token 流。

## Q9. 如何取消正在跑的 Agent？
**参考答案：** 协作式取消，节点边界停止，状态 aborted。

## Q10. 观测里为什么没有 Grafana？
**参考答案：** ADR-005：单机成本；用 JSON 日志+/metrics+管理页。

## Q11. 评测 87% 和 100% 怎么解释？
**参考答案：** v1 回归 100%；v2 冻结集含更严合成期望，报告如实写失败。

## Q12. 如何保证付费不被误调用？
**参考答案：** ALLOW_PAID_API 等多重守卫；设置页不回传明文 Key。

## Q13. 日志如何防泄密？
**参考答案：** redact 注册密钥与形状匹配；单测覆盖。

## Q14. 三角色各能做什么？
**参考答案：** admin 管理/审批；support 工单流转；user 本人数据。

## Q15. 最后管理员保护？
**参考答案：** 降级/停用前 FOR UPDATE 计数，禁止去掉最后一个 active admin。

## Q16. 镜像如何非 root？
**参考答案：** backend USER app；frontend_prod nginx-unprivileged。

## Q17. 测试库误清主库怎么防？
**参考答案：** --prepare 要求库名 _test/_eval。

## Q18. 工具失败如何降级？
**参考答案：** 故障注入场景与 degraded 路径；评测含 tool_error。

## Q19. 前端 token 放哪？
**参考答案：** sessionStorage；401 统一登出。

## Q20. 开源前还要做什么？
**参考答案：** Teddy 授权 push/可见性、确认 LICENSE、轮换曾暴露过的 Key。
