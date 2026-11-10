from tests.model_stub import classification_response
"""Daily orchestration integration tests with only external sources replaced."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from daily_trending.daily import run_daily
from daily_trending.dashscope import DashScopeClient, DashScopeConfig
from daily_trending.scheduler import BEIJING


class DailyTests(unittest.TestCase):
    def test_complete_daily_run_publishes_once_and_repeat_trigger_makes_no_calls(self):
        calls = []

        def transport(payload, _key):
            calls.append(payload)
            return classification_response(payload)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = DashScopeClient(api_key="test", transport=transport, config=DashScopeConfig(max_retries=0))
            options = {
                "clock": lambda: datetime(2026, 11, 10, 8, tzinfo=BEIJING),
                "client": client,
                "fetch": lambda _: (Path(__file__).parent / "fixtures/trending_page.html").read_text(encoding="utf-8"),
                "source_loader": lambda _: {},
            }
            path = run_daily(root / "site", root / "state", **options)
            saved = path.read_bytes()
            self.assertEqual(json.loads(saved)["status"], "published")
            self.assertEqual(json.loads(saved)["published_at"], "2026-11-10T00:00:00+00:00")
            self.assertNotIn("样本预览", path.with_name("index.html").read_text(encoding="utf-8"))
            self.assertEqual(len(calls), 1)
            self.assertEqual(run_daily(root / "site", root / "state", **options), path)
            self.assertEqual(len(calls), 1)
            self.assertEqual(path.read_bytes(), saved)

    def test_expired_daily_run_makes_no_external_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(TimeoutError):
                run_daily(
                    root / "site",
                    root / "state",
                    clock=lambda: datetime(2026, 11, 10, 9, tzinfo=BEIJING),
                    fetch=lambda _: self.fail("late collection"),
                )
            self.assertFalse((root / "site/archive").exists())

    def test_crossing_deadline_before_model_stops_spending_and_publication(self):
        clock = [datetime(2026, 11, 10, 8, 59, tzinfo=BEIJING)]

        def source_loader(_):
            clock[0] = datetime(2026, 11, 10, 9, tzinfo=BEIJING)
            return {}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = DashScopeClient(
                api_key="test",
                config=DashScopeConfig(max_retries=0),
                transport=lambda *_: self.fail("paid request after deadline"),
            )
            with self.assertRaises(ValueError):
                run_daily(
                    root / "site",
                    root / "state",
                    clock=lambda: clock[0],
                    client=client,
                    fetch=lambda _: (Path(__file__).parent / "fixtures/trending_page.html").read_text(encoding="utf-8"),
                    source_loader=source_loader,
                )
            self.assertEqual(client.calls_used, 0)
            self.assertFalse((root / "site/archive").exists())

    def test_lost_state_and_overlapping_run_fail_before_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "site/archive").mkdir(parents=True)
            options = {
                "clock": lambda: datetime(2026, 11, 10, 8, tzinfo=BEIJING),
                "fetch": lambda _: self.fail("unsafe collection"),
            }
            with self.assertRaisesRegex(ValueError, "state is missing"):
                run_daily(root / "site", root / "state", **options)
            (root / "state/initialized").touch()
            (root / "state/run.lock").mkdir()
            with self.assertRaisesRegex(RuntimeError, "already active"):
                run_daily(root / "site", root / "state", **options)

    def test_source_failure_retries_three_times_and_preserves_initial_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def fetch(_):
                calls.append("fetch")
                raise OSError("upstream unavailable")

            with self.assertRaisesRegex(OSError, "upstream unavailable"):
                run_daily(
                    root / "site", root / "state", clock=lambda: datetime(2026, 11, 10, 8, tzinfo=BEIJING), fetch=fetch
                )
            self.assertEqual(calls, ["fetch", "fetch", "fetch"])
            self.assertIn("尚未发布", (root / "site/index.html").read_text(encoding="utf-8"))
            self.assertFalse((root / "site/archive").exists())
            self.assertFalse((root / "state/run.lock").exists())
