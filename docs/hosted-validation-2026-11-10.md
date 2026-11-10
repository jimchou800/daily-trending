# Hosted validation — 2026-11-10

Public origin: <https://daily-trending-authority.zhoumin808.workers.dev>.
The account owner confirmed Workers Free. CLI OAuth authentication and the
existing workers.dev subdomain were verified. The OAuth permission could not
read billing subscriptions (HTTP403); Free-plan confirmation is owner-supplied,
not a billing API audit. No paid plan or R2 was enabled.

The Worker and SQLite Durable Object binding deployed successfully. A pinned
installation was explicitly initialized with separate automation/bootstrap
credentials; the temporary bootstrap secret was then deleted. Credentials are
not stored in this repository. The initial monthly ledger contained zero spend,
zero attempts and no pending reservations. Development model experiments have
their own recorded cost evidence and did not create public inclusion history.

Observed after the day's 09:00 Beijing cutoff:

- Public homepage HTTP200 with `Cache-Control:no-store`, “尚未发布” and
  “今日日报生成失败”. No overdue development preview was published.
- Archive index HTTP200, without the current-day failure banner.
- Anonymous private-state request HTTP401; public `/report.json` HTTP404.
- Authenticated late `begin` request HTTP409 `deadline_or_date`; it did not
  create a generation attempt or spend model budget.
- Headless Edge checked live pages at 390×844 and 1440×1000: no login,
  expected status text, no horizontal overflow, and archive state independent
  from current-day failure. Screenshots were saved as development artifacts.

The GitHub `production` environment has the two documented publication variables
and the two required secrets configured. Its schedule workflow is on the
integration branch; GitHub scheduled events run from the default branch, so
**automatic generation is not active until the reviewed workflow reaches main**.
No manual generation endpoint or workflow trigger was added.

These observations do not establish successful on-time live publication, hosted
correction/rollback, actual shared quotas, free runner capacity, or exact durable
commit timing. The platform crash-window limitation in
[hosted publication](hosted-publication.md) remains unresolved acceptance work.
There is not yet a public issue to read; the initiator's seven-issue trial has
not started. Do not mark those acceptance conditions complete from this smoke.
