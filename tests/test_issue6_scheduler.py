import json
import re
import subprocess
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

    def test_scheduler_reports_retry_exhaustion_and_ready_state(self):
        scheduler = BeijingScheduler("2026-11-09", now=lambda: datetime(2026, 11, 9, 8, tzinfo=BEIJING))
        self.assertEqual(scheduler.status()["status"], "ready")
        for _ in range(3):
            self.assertTrue(scheduler.start_attempt())
        self.assertEqual(scheduler.status()["status"], "retry_exhausted")

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

    def test_future_index_does_not_mask_today_missing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.json").write_text(json.dumps({"latest_report_date": "2026-11-10"}), encoding="utf-8")
            state = homepage_status(root, datetime(2026, 11, 9, 9, tzinfo=BEIJING))
            self.assertEqual(state["status"], "failed")

    def test_homepage_html_rechecks_status_using_beijing_browser_time(self):
        page = render_homepage("<!doctype html><html><body><main>latest</main></body></html>")
        self.assertIn("Asia/Shanghai", page)
        self.assertIn("今日日报生成失败", page)
        self.assertIn("尚未发布", page)
        self.assertIn("index.json", page)

    def test_homepage_requires_actual_timely_timezone_aware_publication(self):
        cases = [
            (None, "failed"),
            ("invalid", "failed"),
            ("2026-11-09T08:30:00", "failed"),
            ("2026-11-09T01:00:00Z", "failed"),
            ("2026-11-09T09:01:00+08:00", "failed"),
            ("2026-11-08T23:59:00+08:00", "failed"),
            ("2026-11-09T00:59:59Z", "published"),
            ("2026-11-08T19:59:59-05:00", "published"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for published_at, expected in cases:
                with self.subTest(published_at=published_at):
                    (root / "index.json").write_text(
                        json.dumps({"latest_report_date": "2026-11-09", "published_at": published_at}),
                        encoding="utf-8",
                    )
                    state = homepage_status(root, datetime(2026, 11, 9, 1, tzinfo=timezone.utc))
                    self.assertEqual(state["status"], expected)
            (root / "index.json").write_text(
                json.dumps({"latest_report_date": "2026-11-09", "published_at": "2026-11-09T08:59:00+08:00"}),
                encoding="utf-8",
            )
            self.assertEqual(
                homepage_status(root, datetime(2026, 11, 9, 8, 58, tzinfo=BEIJING))["status"], "unpublished"
            )

    def test_first_missing_report_keeps_unpublished_message_after_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            state = homepage_status(Path(directory), datetime(2026, 11, 9, 9, tzinfo=BEIJING))
            self.assertIn("尚未发布", state["message"])
            self.assertIn("今日日报生成失败", state["message"])

    def _browser_states(self, indices, clocks):
        page = render_homepage("<html><body>latest</body></html>")
        script = re.search(r"<script>(.*?)</script>", page, re.DOTALL).group(1)
        harness = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
let current, index;
const intervals = [];
const element = {textContent: '', hidden: false};
const NativeDate = Date;
class ControlledDate extends NativeDate {
  constructor(...args) { super(...(args.length ? args : [current])); }
  static now() { return new NativeDate(current).getTime(); }
}
const context = vm.createContext({
  Date: ControlledDate, Intl,
  document: {getElementById: () => element},
  fetch: async () => ({ok: true, json: async () => index}),
  setInterval: callback => { intervals.push(callback); }
});
(async () => {
  const states = [];
  for (let i = 0; i < input.clocks.length; i++) {
    current = input.clocks[i];
    index = input.indices[i];
    if (!i) vm.runInContext(input.script, context);
    else intervals.forEach(callback => callback());
    await new Promise(resolve => setImmediate(resolve));
    states.push({...element});
  }
  process.stdout.write(JSON.stringify(states));
})();
"""
        result = subprocess.run(
            ["node", "-e", harness],
            input=json.dumps({"script": script, "indices": indices, "clocks": clocks}),
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
        )
        return json.loads(result.stdout)

    def test_browser_open_across_deadline_and_next_day_updates_without_running_task(self):
        states = self._browser_states(
            [{}, {}, {}],
            ["2026-11-09T00:59:59Z", "2026-11-09T01:00:00Z", "2026-11-09T16:00:00Z"],
        )
        self.assertEqual(states[0]["textContent"], "尚未发布")
        self.assertIn("今日日报生成失败", states[1]["textContent"])
        self.assertIn("尚未发布", states[1]["textContent"])
        self.assertEqual(states[2]["textContent"], "尚未发布")
        self.assertFalse(states[2]["hidden"])

    def test_browser_validates_publication_and_clears_stale_failure_banner(self):
        valid = {"latest_report_date": "2026-11-09", "published_at": "2026-11-09T00:59:59Z"}
        invalid = [
            None,
            "invalid",
            "2026-11-09T08:30:00",
            "2026-11-09T01:00:00Z",
            "2026-11-09T09:01:00+08:00",
            "2026-11-08T23:59:00+08:00",
        ]
        for published_at in invalid:
            with self.subTest(published_at=published_at):
                states = self._browser_states(
                    [{**valid, "published_at": published_at}, valid],
                    ["2026-11-09T01:00:00Z"] * 2,
                )
                self.assertIn("今日日报生成失败", states[0]["textContent"])
                self.assertEqual(states[1], {"textContent": "", "hidden": True})
        states = self._browser_states(
            [valid, valid, valid],
            ["2026-11-09T00:58:00Z", "2026-11-09T01:00:00Z", "2026-11-10T01:00:00Z"],
        )
        self.assertEqual(states[0]["textContent"], "尚未发布")
        self.assertTrue(states[1]["hidden"])
        self.assertIn("今日日报生成失败", states[2]["textContent"])
        states = self._browser_states(
            [{**valid, "published_at": "2026-11-08T19:59:59-05:00"}],
            ["2026-11-09T01:00:00Z"],
        )
        self.assertEqual(states[0], {"textContent": "", "hidden": True})

    def test_non_object_index_cannot_hide_missing_report(self):
        for index in (None, [], 1):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
                self.assertEqual(homepage_status(root, datetime(2026, 11, 9, 9, tzinfo=BEIJING))["status"], "failed")
                states = self._browser_states([index], ["2026-11-09T01:00:00Z"])
                self.assertIn("今日日报生成失败", states[0]["textContent"])
                self.assertIn("尚未发布", states[0]["textContent"])

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

        late = BeijingScheduler("2026-11-09", now=lambda: datetime(2026, 11, 9, 9, tzinfo=BEIJING))
        with self.assertRaises(TimeoutError):
            run_with_retries(late, lambda _: self.fail("late attempt must not run"))


if __name__ == "__main__":
    unittest.main()
