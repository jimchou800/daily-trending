"""Clock-aware daily run policy and independent homepage missing-report state."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable

from .contracts import require, require_date

BEIJING = timezone(timedelta(hours=8))
MAX_ATTEMPTS = 3  # initial attempt plus two retries


def publication_deadline(report_date: str) -> datetime:
    """Return the strict Beijing 09:00 deadline for a report date."""
    require_date(report_date, "report_date")
    return datetime.combine(datetime.fromisoformat(report_date).date(), time(9), BEIJING)


class BeijingScheduler:
    """Apply deadline, retry, and monthly budget policy to one report period."""

    def __init__(
        self,
        report_date: str,
        *,
        now: Callable[[], datetime] | None = None,
        monthly_budget_cny: float = 30.0,
        spent_cny: float = 0.0,
    ) -> None:
        require_date(report_date, "report_date")
        require(monthly_budget_cny >= 0 and spent_cny >= 0, "budget must be non-negative")
        self.report_date = report_date
        self._now = now or (lambda: datetime.now(BEIJING))
        self.monthly_budget_cny = monthly_budget_cny
        self.spent_cny = spent_cny
        self.attempts = 0

    def current_time(self) -> datetime:
        """Return the injected clock converted to Beijing time."""
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=BEIJING)
        return value.astimezone(BEIJING)

    def before_deadline(self) -> bool:
        """Return whether this period may still perform generation or publication."""
        return self.current_time() < publication_deadline(self.report_date)

    def can_retry(self) -> bool:
        """Return whether another attempt is allowed by time, count, and budget."""
        return self.before_deadline() and self.attempts < MAX_ATTEMPTS and self.spent_cny < self.monthly_budget_cny

    def start_attempt(self, estimated_cost_cny: float = 0.0) -> bool:
        """Reserve one attempt before external work; reject late or over-budget work."""
        require(estimated_cost_cny >= 0, "estimated cost must be non-negative")
        if not self.before_deadline() or self.attempts >= MAX_ATTEMPTS:
            return False
        if self.spent_cny + estimated_cost_cny > self.monthly_budget_cny:
            return False
        self.attempts += 1
        self.spent_cny = round(self.spent_cny + estimated_cost_cny, 6)
        return True

    def status(self) -> dict[str, object]:
        """Return an inspectable internal status for logs and scheduler dashboards."""
        if self.current_time().date() > datetime.fromisoformat(self.report_date).date() or not self.before_deadline():
            status = "expired"
            message = "今日期刊已过 09:00 截止时间"
        elif self.spent_cny >= self.monthly_budget_cny:
            status = "budget_exhausted"
            message = "当月生成额度已用尽"
        elif self.attempts >= MAX_ATTEMPTS:
            status = "retry_exhausted"
            message = "本期期刊重试次数已用尽"
        else:
            status = "ready"
            message = ""
        return {"status": status, "message": message, "attempts": self.attempts, "spent_cny": self.spent_cny}


def run_with_retries(
    scheduler: BeijingScheduler,
    attempt: Callable[[int], object],
    *,
    estimated_cost_cny: float = 0.0,
) -> object:
    """Run a bounded operation, stopping before the hard deadline.

    ``attempt`` receives a one-based attempt number.  The last operational
    error is re-raised after the initial call and two retries; a period that is
    already late raises ``TimeoutError`` without invoking the callback.
    """
    last_error: Exception | None = None
    while scheduler.can_retry():
        if not scheduler.start_attempt(estimated_cost_cny):
            break
        try:
            return attempt(scheduler.attempts)
        except Exception as exc:  # boundary for the scheduled operation
            last_error = exc
    if last_error is not None:
        raise last_error
    raise TimeoutError("report period is past its publication deadline or budget")


def homepage_status(site_root: Path, now: datetime | None = None) -> dict[str, object]:
    """Compute current homepage state from published index data and Beijing time.

    The task's run state is deliberately not read: a missing, failed, or never
    started task produces the same public result after the hard deadline, while
    the last successfully published date remains visible.
    """
    current = now or datetime.now(BEIJING)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BEIJING)
    current = current.astimezone(BEIJING)
    index_path = Path(site_root) / "index.json"
    latest: str | None = None
    published_at: str | None = None
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        latest = data.get("latest_report_date")
        published_at = data.get("published_at")
        if latest is not None:
            require_date(latest, "latest_report_date")
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        latest = None
        published_at = None
    today = current.date()
    if latest and datetime.fromisoformat(latest).date() >= today:
        return {"status": "published", "latest_report_date": latest, "published_at": published_at, "message": ""}
    if current.time() < time(9):
        return {"status": "published" if latest else "unpublished", "latest_report_date": latest, "published_at": published_at, "message": "" if latest else "尚未发布"}
    return {"status": "failed", "latest_report_date": latest, "published_at": published_at, "message": "今日日报生成失败"}


def render_homepage(report_html: str) -> str:
    """Add a browser-clock status banner that handles missed runs without task state."""
    banner = '<p id="daily-status" role="status" aria-live="polite"></p>'
    script = """<script>
(() => {
  const status = document.getElementById('daily-status');
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', hourCycle: 'h23'
  }).formatToParts(new Date());
  const value = key => parts.find(part => part.type === key).value;
  const today = `${value('year')}-${value('month')}-${value('day')}`;
  const afterDeadline = Number(value('hour')) >= 9;
  fetch('./index.json', { cache: 'no-store' }).then(response => response.ok ? response.json() : {})
    .catch(() => ({})).then(index => {
      if (index.latest_report_date === today) return;
      status.textContent = afterDeadline ? '今日日报生成失败' : (index.latest_report_date ? '' : '尚未发布');
      status.hidden = !status.textContent;
    });
})();
</script>"""
    if "</body>" not in report_html:
        return report_html + banner + script
    return report_html.replace("</body>", f"{banner}{script}</body>", 1)
