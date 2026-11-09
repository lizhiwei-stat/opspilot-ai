# 10 个追问设计题

## F1. 如果日请求到 100 万次，架构怎么变？
**答：** 先垂直扩容与连接池；读多可副本；再考虑队列与独立 worker。当前刻意单机 Compose，不假装已有。

## F2. 向量维度切换怎么办？
**答：** 按 embedding_model 隔离索引，切换后重建，不混用。

## F3. 如何做多租户？
**答：** 今日以 KB ACL+角色模拟；真多租户需 tenant_id 贯穿与强隔离，未实现。

## F4. 模型换成 Claude 要改什么？
**答：** 走 openai_compatible 网关与价格表；守卫不变。

## F5. 如何证明没有审批绕过？
**答：** 自动化测试：alice POST approval → 403；审计行。

## F6. 评测集如何避免泄漏到训练？
**答：** 本项目 Mock 规则非训练；真实模型应用 held-out/冻结集。

## F7. SSE 代理超时？
**答：** nginx 对 /events 关缓冲、长 timeout；见 infra/nginx.conf。

## F8. 如何做金丝雀发布？
**答：** 本仓库仅本地；思路是镜像 tag+端口并存，未做公网。

## F9. secret 进了 git 怎么办？
**答：** 禁止改史除非授权；轮换密钥；本仓库扫描见 stage7_scan。

## F10. 为什么不用 LangChain AgentExecutor？
**答：** 要审批中断与显式节点控制，LangGraph 更贴。
