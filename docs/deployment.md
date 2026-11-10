# Daily deployment

The local orchestration entry point is `python -m daily_trending.daily`. It uses `site/` as the authoritative local publication directory and `.daily-trending-state/` as durable internal state. It supports an initial attempt and at most two retries before 09:00 Beijing time. An exclusive process lock prevents overlapping runs, and a lost state directory with an existing archive fails closed.

Production deployment remains unverified. No automatic deployment workflow is enabled. In particular, a local file publication is not evidence of a completed GitHub Pages deployment.

Before enabling a scheduled hosted run, its adapter must provide durable authoritative snapshots, budget reservations and successful publication history. Missing storage must not reset spending, remove archives or let failed deployment candidates become successful history. Publication completion must use the actual hosted commit time and reject late commits. Record trigger delay, completion time, pricing and monthly capacity against the spec.

Actions cache is unsuitable as the authoritative state store: cache eviction and immutable cache keys can lose reservations and archives. Cache contents must not be described as confidential storage. Keep `AI_DASHSCOPE_API_KEY` in the execution environment; never copy it into public artifacts or logs.
