# 标注变更日志（held-out）

规则：不得为提高分数修改 gold；只有"标注本身确实错误"才允许改，且必须在此登记（原因、前后哈希、前后分数）。

## LC-001 · 2026-11-02 · h003 gold 短语空格笔误

- 变更：`"contains": "down 迁移脚本"` → `"contains": "down迁移脚本"`（仅删除一个空格；文档、query、类别、expect_answer 均未改）
- 原因：语料 `deployment-rollback-guide.pdf` 原文为"需要先执行对应的 down迁移脚本"（无空格）。gold 校验（`verify_gold`）在**任何 held-out 检索运行之前**即失败，评测脚本拒绝运行。这是字符串匹配笔误，不是相关性判断的改变——相关文档、相关段落都没变。
- 前后分数：变更前 held-out **从未成功运行过**（gold 校验直接中止，无任何分数），因此不存在"改标签涨分"。变更后的首次运行即为 held-out 正式结果。
- 冻结哈希：旧 `9b73a1f739b8e449b11c259a7bfc3e8197a99649400e4e1c486cbdeaad48df88` → 新见 `FROZEN.sha256`。
- 同时：`verify_gold` 改为一次性列出全部失败短语（离线核对确认仅 h003 一处）。
