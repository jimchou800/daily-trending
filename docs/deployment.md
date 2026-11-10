# Hosted daily deployment

`python -m daily_trending.hosted_daily` runs the Python generator against one
Cloudflare Worker / SQLite Durable Object authority. Its temporary directory is
only a disposable preview. The Worker owns attempts, snapshots, source and model
caches, reservations, monthly accounting, successful inclusion history and the
atomic public pointer. Neither Actions cache nor filesystem uploads are used as
production state. `python -m daily_trending.daily` remains the local sample runner;
its local publication result is not evidence of a hosted deployment.

## Installation and environment

Deploy `worker/` with a stable `PUBLICATION_AUTHORITY` namespace and
`PUBLICATION_OBJECT_NAME`. Configure `PUBLICATION_INSTALLATION_ID` and secret
`PUBLICATION_AUTOMATION_TOKEN` on the Worker. For the first installation only,
configure a separate temporary `PUBLICATION_BOOTSTRAP_TOKEN`, initialize through
`POST /_automation/install` with that token and the normal automation headers,
then remove the bootstrap binding. Never initialize a replacement ledger for an
existing archive. Missing/corrupt existing identity must fail closed.

In the GitHub `production` environment configure these **names**, without
putting their values in repository files:

- Variable `PUBLICATION_BASE_URL`: the deployed HTTPS origin, without a path.
- Variable `PUBLICATION_INSTALLATION_ID`: the pinned existing installation ID.
- Secret `PUBLICATION_AUTOMATION_TOKEN`: the matching automation credential.
- Secret `AI_DASHSCOPE_API_KEY`: DashScope credential for `dashscope/qwen3.8-max`.

Worker deployment additionally needs the operator's `CLOUDFLARE_ACCOUNT_ID` and
scoped `CLOUDFLARE_API_TOKEN` where applicable. The Python scheduled job does not
need deployment credentials. No workflow creates accounts or enables paid
hosting. Do not print credentials or upload source/model/control artifacts as
public CI artifacts.

## Schedule and recovery

`.github/workflows/daily.yml` schedules at 00:15 UTC / 08:15 Beijing. GitHub cron
may be delayed or dropped; this is an early operational allowance, not a delivery
guarantee. The runner derives today's Beijing date from its actual clock and
rejects late execution. The server independently enforces today's date and the
09:00 cutoff. Its public homepage independently shows missing-today status even
when no job runs. Archives retain their real dates and have no missing-today
banner. The workflow has no manual generation trigger and serializes deliveries;
server fencing remains necessary for overlapping or externally repeated jobs.

Every distinct generation attempt uses remote `begin`; at most three attempts
(initial plus two retries) survive process restarts. Each paid HTTP transport
attempt requires a durable affirmative `reserve` acknowledgement. A lost
reservation acknowledgement is queried but never grants paid dispatch. Unknown
outcomes remain charged. Provider usage is written through `usage` before a
response is returned to analysis, even after the deadline; invalid/underestimated
usage latches monthly accounting closed. A failed usage acknowledgement stops
further calls. Successful source/relevance/detail caches and the first complete
snapshot are immutable and reused after runner loss. The server independently
validates selection, metrics, snapshot and successful history before publication.

Stage/publish acknowledgements only count as success when the server returns the
canonical committed report. After a lost publish acknowledgement the runner
reads authoritative publication state, including after cutoff, without creating
another issue. Server publication uses an early safety margin and guarded
persistence; local clock tests cannot establish an exact hosted commit timestamp.
Do not recover by deleting authority state, resetting the month, backfilling a
missed date or changing the installation/object identity. Inspect private state
and preserve unresolved reservations; existing public pages are independent of
model-budget admission.

## Verification and remaining acceptance

Local offline verification uses `python -m unittest tests.test_hosted`: actual
parser, relevance/detail analysis, selection, JSON/rendering and remote adapter
run with substituted external HTTP providers. Worker checks are documented in
`worker/`. Full quality gates are in `.github/workflows/quality.yml`.

Production deployment, account Free eligibility and shared quotas, GitHub runner
allocation, real trigger delay, before/after-sync deadline observations,
mobile/desktop public reads and model price/capacity feasibility must be recorded
from the actual environment. No deployment or paid model request is established
by the offline tests. Worker Free quotas are account-shared; quota exhaustion can
affect public availability. Do not select the paid Workers plan or enable R2
without authorization. The CNY30 monthly hard admission limit protects spending;
it does not by itself prove that `qwen3.8-max` can generate every day. Verify the
applicable region/tariff/token limits and conservative 31-day retry envelope
before running paid traffic. Initiator reading of seven issues, with at least
five valuable issues within ten minutes and factual checks for all seven, remains
separate product acceptance.

The initial deployed smoke results and remaining limits are recorded in
[hosted validation](hosted-validation-2026-11-10.md). Model experiments and their
actual token-based cost calculations are recorded in
[model validation](model-validation-2026-11-10.md).
