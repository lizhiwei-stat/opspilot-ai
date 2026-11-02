# 数据库连接池调优指南（HikariCP）

## 关键参数

- maximumPoolSize：最大连接数。经验公式：连接数 ≈ CPU 核数 × 2 + 有效磁盘数。订单服务生产环境推荐 50。
- connectionTimeout：获取连接的最长等待时间，默认 30000ms。超时会抛出 "Connection is not available"。
- leakDetectionThreshold：连接泄漏检测阈值，建议 20000ms，超过该时间未归还的连接会打印堆栈。
- maxLifetime：连接最大存活时间，应比数据库的 wait_timeout 小 30 秒以上。

## 连接池耗尽的典型原因

1. 慢查询占用连接：单条 SQL 超过 1 秒即应视为慢查询，需要加索引或拆分。
2. 连接泄漏：代码中获取连接后在异常分支没有关闭。
3. 流量突增：大促期间并发上升而连接数没有同步扩容。
4. 事务过长：在事务内调用外部 HTTP 服务（例如支付网关），网络延迟会长时间占用连接。

## 排查命令

在 PostgreSQL 中查看当前连接与等待：
SELECT state, count(*) FROM pg_stat_activity GROUP BY state;
查看运行超过 5 秒的语句：
SELECT pid, now() - query_start AS duration, query FROM pg_stat_activity WHERE state = 'active' AND now() - query_start > interval '5 seconds';

## 处理建议

紧急情况下可以先扩容应用实例分摊连接，但要注意数据库 max_connections 上限。根本解决需要优化慢查询，并避免在事务中做远程调用。
