# 三 Agent PRD v0.1（历史草案）

状态：已被[项目规格 v1.2](project-vision.md)、[编码规范](coding-standards.md)及根目录 [AGENTS.md](../AGENTS.md)替代，不作为当前运行或验收依据。归档标记日期：2026-11-10。

原草案的 UTC 00:00 触发、三维标签及 Markdown 输出描述早于现行规格。当前以北京时间 09:00 为发布硬截止，以结构化 JSON 为统一数据源并发布公共网页；Agent 按 AGENTS.md 第 11 节承担实现、验证和审查职责。发起人已于 2026-11-10 确认阅读满意，取消连续 7 期试读门槛并接受当前首版，见[验收记录](../docs/acceptance-trial.md)。

以下保留原文，供追溯最初构想：

# AI 知识库 · 三 Agent PRD v0.1

## 总流程
每天 UTC 0:00 触发 · collector → analyzer → organizer · 串行。

## Agent 职责
- collector: 抓 GitHub Trending  · 存 knowledge/raw/
- analyzer: 读 raw · 给每条打 3 维度标签
- organizer: 读已标注 · 整理成 MD

## 开放问题（? 用 to-issues 细化成任务）
- 上游失败下游怎么办？
- 数据怎么传？文件 or 消息？
- 重跑策略？
- 进度追踪？
