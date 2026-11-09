import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daily_trending.scheduler import (
    BeijingScheduler,
    homepage_status,
    publication_deadline,
    run_with_retries,
    render_homepage,
)


BEIJING = timezone(timedelta(hours=8))


class SchedulerTests(unittest.TestCase):
    def test_deadline_is_beijing_nine_and_is_strict(self):
        deadline = publication_deadline("2026-11-09")
        self.assertEqual(deadline, datetime(2026, 11, 9, 9, tzinfo=BEIJING))
        scheduler = BeijingScheduler("2026-11-09", now=lambda: datetime(2026, 11, 9, 8, 59, tzinfo=BEIJING))
        self.assertTrue(scheduler.before_deadline())
        scheduler = BeijingScheduler("2026-11-09", now=lambda: deadline)
        self.assertFalse(scheduler.before_deadline())

    def test_retry_budget_allows_initial_plus_two_and_stops_after_deadline(self):
        clock = [datetime(2026, 11, 9, 8, 0, tzinfo=BEIJING)]
        scheduler = BeijingScheduler("2026-11-09", now=lambda: clock[0], monthly_budget_cny=1.0)
        self.assertTrue(scheduler.start_attempt(0.30))
        self.assertTrue(scheduler.start_attempt(0.30))
        self.assertTrue(scheduler.start_attempt(0.30))
        self.assertFalse(scheduler.start_attempt(0.01))
        clock[0] = datetime(2026, 11, 9, 9, 0, tzinfo=BEIJING)
        self.assertFalse(scheduler.can_retry())
        self.assertFalse(scheduler.start_attempt(0.01))

    def test_budget_exhaustion_prevents_new_attempt(self):
        scheduler = BeijingScheduler(
            "2026-11-09",
            now=lambda: datetime(2026, 11, 9, 8, tzinfo=BEIJING),
            monthly_budget_cny=0.5,
        )
        self.assertTrue(scheduler.start_attempt(0.5))
        self.assertEqual(scheduler.status()["message"], "当月生成额度已用尽")
        self.assertFalse(scheduler.start_attempt(0.01))

    def test_homepage_reports_never_published_and_missing_without_touching_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                homepage_status(root, datetime(2026, 11, 9, 8, 59, tzinfo=BEIJING))["status"],
                "unpublished",
            )
            self.assertEqual(
                homepage_status(root, datetime(2026, 11, 9, 9, tzinfo=BEIJING))["status"],
                "failed",
            )
            (root / "index.json").write_text(
                json.dumps({"latest_report_date": "2026-11-08", "published_at": "2026-11-08T01:00:00Z"}),
                encoding="utf-8",
            )
            state = homepage_status(root, datetime(2026, 11, 9, 9, tzinfo=BEIJING))
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["latest_report_date"], "2026-11-08")

    def test_homepage_html_rechecks_status_using_beijing_browser_time(self):
        page = render_homepage("<!doctype html><html><body><main>latest</main></body></html>")
        self.assertIn("Asia/Shanghai", page)
        self.assertIn("今日日报生成失败", page)
        self.assertIn("尚未发布", page)
        self.assertIn("index.json", page)

    def test_old_run_cannot_publish_on_next_day(self):
        scheduler = BeijingScheduler("2026-11-08", now=lambda: datetime(2026, 11, 9, 8, tzinfo=BEIJING))
        self.assertFalse(scheduler.before_deadline())
        self.assertEqual(scheduler.status()["status"], "expired")

    def test_run_retries_transient_failure_but_never_starts_after_cutoff(self):
        clock = [datetime(2026, 11, 9, 8, 0, tzinfo=BEIJING)]
        scheduler = BeijingScheduler("2026-11-09", now=lambda: clock[0])
        calls = []

        def attempt(index):
            calls.append(index)
            if index < 3:
                raise RuntimeError("temporary failure")
            return "published"

        self.assertEqual(run_with_retries(scheduler, attempt), "published")
        self.assertEqual(calls, [1, 2, 3])

        late = BeijingScheduler(
            "2026-11-09", now=lambda: datetime(2026, 11, 9, 9, tzinfo=BEIJING)
        )
        with self.assertRaises(TimeoutError):
            run_with_retries(late, lambda _: self.fail("late attempt must not run"))


if __name__ == "__main__":
    unittest.main()
