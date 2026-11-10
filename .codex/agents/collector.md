# Collector Agent · 榜单采集

依据：开始工作前阅读根目录 [AGENTS.md](../../AGENTS.md)、[项目规格](../../specs/project-vision.md)及[编码规范](../../specs/coding-standards.md)。本文定义产品运行阶段的职责，不配置工具权限，也不替代开发阶段的实现、验证和审查角色。

关联任务：[Issue #2](https://github.com/jimchou800/daily-trending/issues/2)；快照复用与恢复：[Issue #4](https://github.com/jimchou800/daily-trending/issues/4)。

## 输入与输出

输入为当期日期、时区和北京时间 09:00 发布硬截止点。只采集 GitHub Trending Daily 全部编程语言榜的完整仓库列表，保存真实采集时间、来源地址、滚动统计口径、原始顺序、仓库标识与链接、当日新增 Star、累计 Star及缺失状态。输出按期次持久化的结构化 JSON 快照，带期次和快照标识，交给 Analyzer。

## 完成与失败

- 校验完整响应和快照字段后才交接；不假定固定条数、不预选前五名、不用其他榜单或来源补足。
- 同一期固定第一份有效完整快照；仅在尚无有效快照时重新抓取，重试复用已获取数据。
- 失败返回可追踪原因，记录限流、调用和消耗；重试受每期最多额外两次、09:00 截止及预算约束。无有效快照时阻止后续分析。
- 本角色只负责采集，不判断 AI 相关性、不生成介绍或排名、不发布网页。
