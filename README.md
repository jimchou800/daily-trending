面向有技术背景的普通读者，每天用不超过 10 分钟，了解 GitHub Trending 上值得关注的 AI 项目、热度证据，以及有可靠依据的走红原因。

首要目标是掌握热点。发现新项目、筛选实用工具是辅助价值，不作为排序依据。产品覆盖 GitHub Trending 中的 AI 热点，不承诺覆盖整个 AI 行业或所有热门开源项目。

首版为无需登录的公共静态网站。所有人阅读同一份简体中文日报，可浏览最新一期及历史归档。项目名和必要的英文术语保留原文。每天自动生成并发布，无需人工审核。

开发使用 Python 3.12；托管端与浏览器状态测试使用 Node.js 22。安装 `requirements-dev.txt` 后执行：

```text
black --check daily_trending tests scripts
ruff check daily_trending tests scripts
python -m coverage run --branch -m daily_trending.quality --run-tests
python -m coverage json -o coverage.json
python scripts/check_coverage.py
```

托管端在 `worker/` 执行 `npm ci` 和 `npm run check`，分别检查 TypeScript 与实际浏览器脚本的覆盖率。上述自动测试使用保存样本和外部边界替身，不调用付费模型。真实模型密钥从 `AI_DASHSCOPE_API_KEY` 读取，配置保留 `dashscope/qwen3.8-max`；已有[真实调用与费用记录](docs/model-validation-2026-11-10.md)，不等于完整月度验收。

本地日任务入口为 `python -m daily_trending.daily`，会进行真实采集和模型调用。线上入口为 `python -m daily_trending.hosted_daily`，权威状态与预算由远端持久保存；启用前遵循[部署约束](docs/deployment.md)。[公共网站](https://daily-trending-authority.zhoumin808.workers.dev)已部署，定时工作流在默认分支 main 生效，每天北京时间 08:15 启动；[上线验证记录](docs/hosted-validation-2026-11-10.md)列明已验证和未验证范围。发起人已于 2026-11-10 确认阅读满意并接受当前首版，取消连续 7 期试读要求，见[验收记录](docs/acceptance-trial.md)；自动检查、上线实测和产品验收分别记录。
