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
The initiator's seven-issue trial is not complete. Do not mark those acceptance
conditions complete from this smoke.

## Explicit one-day maintenance publication

The maintainer subsequently requested one late publication for 2026-11-10 to
inspect the result. This is an explicit exception for that date, not a change
to the normal 09:00 deadline or no-backfill policy.

The saved complete Daily snapshot was collected at
`2026-11-10T02:05:14.043696+00:00` (10:05 Beijing). Five entries were published
at `2026-11-10T03:26:51.068Z` (11:26 Beijing), retaining original ranks
1, 5, 6, 7 and 8. The public report is at
<https://daily-trending-authority.zhoumin808.workers.dev/archive/2026-11-10/>.
It explicitly states that maintenance publication missed 09:00. This is not
evidence of on-time publication acceptance. No additional model calls were made
to publish the already generated report.

The maintenance route required normal automation credentials, a separate
temporary token, the server's current date, the fixed date 2026-11-10 and the
SHA-256 of the saved report. Snapshot, selection, metrics and history validation
remained active. The temporary token was deleted and date/hash bindings removed
after publication; an authenticated request then returned HTTP403. The disabled
route remains in code, without active authorization.

Live headless Edge verification at 390x844 and 1440x1000 confirmed HTTP200 for
the homepage and dated report, all five entries in original order, the real
timestamp and maintenance note, a single archive link, no horizontal overflow
and no page script errors. Worker checks passed with 46 SQLite tests and 18
browser scenarios; a further targeted midnight rollback assertion passed.
Line coverage was 97.73%, branch coverage 93.13%; emitted browser script coverage
was 100% for both. Independent maintenance-route review found no blockers.

After every issue meets its acceptance conditions, the maintainer authorized
merging the PR, closing the corresponding GitHub issues, and cleaning inactive
merged branches/worktrees and verified intermediate untracked files. Preserve
uncommitted user work, runtime credentials and required deployment state.
