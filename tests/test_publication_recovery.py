from tests.model_stub import classification_response
"""Failures between publication writes must not become successful or backfilled issues."""

import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from daily_trending import publisher
from daily_trending.daily import run_daily
from daily_trending.dashscope import DashScopeClient, DashScopeConfig
from daily_trending.scheduler import BEIJING


def report(date):
    return {
        "report_date": date,
        "status": "published",
        "contract_version": "1.0",
        "timezone": "Asia/Shanghai",
        "collected_at": date + "T00:00:00Z",
        "source": "https://github.com/trending?since=daily",
        "scope": "Daily",
        "entry_count": 0,
        "entries": [],
    }


def publish(root, date):
    return publisher.publish_report(
        report(date),
        "<main>" + date + "</main>",
        root,
        date + "T00:00:00Z",
        now=datetime.fromisoformat(date + "T08:00:00+08:00"),
    )


class PublicationRecoveryTests(unittest.TestCase):
    def test_new_day_discards_interrupted_old_transaction_and_preserves_previous_issue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publish(root, "2026-11-08")
            original = (root / "archive/2026-11-08/report.json").read_bytes()
            write = publisher._write_json_atomic

            def fail_index(path, value):
                if path.name == "index.json":
                    raise OSError("disk interruption")
                write(path, value)

            with patch.object(publisher, "_write_json_atomic", side_effect=fail_index):
                with self.assertRaises(OSError):
                    publish(root, "2026-11-09")
            publish(root, "2026-11-10")
            self.assertEqual(
                json.loads((root / "index.json").read_text())["archive_dates"], ["2026-11-10", "2026-11-08"]
            )
            self.assertFalse((root / "archive/2026-11-09/report.json").exists())
            self.assertEqual(
                [r["report_date"] for r in publisher.load_successful_history(root)], ["2026-11-10", "2026-11-08"]
            )
            self.assertEqual((root / "archive/2026-11-08/report.json").read_bytes(), original)

    def test_daily_retry_recovers_before_short_circuit_without_more_paid_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def transport(payload, _key):
                calls.append(payload)
                return classification_response(payload)

            options = dict(
                clock=lambda: datetime(2026, 11, 10, 8, tzinfo=BEIJING),
                client=DashScopeClient(api_key="test", transport=transport, config=DashScopeConfig(max_retries=0)),
                fetch=lambda _: (Path(__file__).parent / "fixtures/trending_page.html").read_text(encoding="utf-8"),
                source_loader=lambda _: {},
            )
            write = publisher._write_json_atomic

            def fail_index(path, value):
                if path.name == "index.json":
                    raise OSError("disk interruption")
                write(path, value)

            with patch.object(publisher, "_write_json_atomic", side_effect=fail_index):
                with self.assertRaises(OSError):
                    run_daily(root / "site", root / "state", **options)
            spent = options["client"].estimated_spend_cny
            options["clock"] = lambda: datetime(2026, 11, 10, 8, 30, tzinfo=BEIJING)
            path = run_daily(root / "site", root / "state", **options)
            self.assertEqual(json.loads((root / "site/index.json").read_text())["latest_report_date"], "2026-11-10")
            self.assertFalse((root / "site" / publisher.TRANSACTION_FILE).exists())
            self.assertTrue(path.exists())
            stamp = "2026-11-10T00:30:00+00:00"
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["published_at"], stamp)
            self.assertEqual(json.loads((root / "site/index.json").read_text())["published_at"], stamp)
            self.assertEqual(publisher.load_successful_history(root / "site")[0]["published_at"], stamp)
            self.assertIn(stamp, path.with_name("index.html").read_text(encoding="utf-8"))
            self.assertIn(stamp, (root / "site/index.html").read_text(encoding="utf-8"))
            self.assertEqual(len(calls), 1)
            self.assertEqual(options["client"].estimated_spend_cny, spent)

    def test_lost_artifact_subtree_fails_before_new_day_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = DashScopeClient(
                api_key="test",
                config=DashScopeConfig(max_retries=0),
                transport=lambda payload, _: classification_response(payload),
            )
            run_daily(
                root / "site",
                root / "state",
                clock=lambda: datetime(2026, 11, 10, 8, tzinfo=BEIJING),
                client=client,
                fetch=lambda _: (Path(__file__).parent / "fixtures/trending_page.html").read_text(encoding="utf-8"),
                source_loader=lambda _: {},
            )
            shutil.rmtree(root / "state/artifacts")
            with self.assertRaisesRegex(ValueError, "durable|ledger"):
                run_daily(
                    root / "site",
                    root / "state",
                    clock=lambda: datetime(2026, 11, 11, 8, tzinfo=BEIJING),
                    client=client,
                    fetch=lambda _: self.fail("collection after losing accounting"),
                )

    def test_expired_transaction_rolls_back_index_home_and_history(self):
        for interrupted in ("index.html", "history.json"):
            with self.subTest(interrupted=interrupted), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                publish(root, "2026-11-08")
                original = {name: (root / name).read_bytes() for name in ("index.json", "index.html", "history.json")}
                replace = publisher.os.replace

                def fail_replace(source, destination):
                    if Path(destination) == root / interrupted:
                        raise OSError("interrupted")
                    replace(source, destination)

                with patch.object(publisher.os, "replace", side_effect=fail_replace):
                    with self.assertRaises(OSError):
                        publish(root, "2026-11-09")
                publisher.recover_publication(root, now=datetime(2026, 11, 9, 9, tzinfo=BEIJING))
                self.assertEqual({name: (root / name).read_bytes() for name in original}, original)
                self.assertFalse((root / "archive/2026-11-09/report.json").exists())
                self.assertFalse((root / publisher.TRANSACTION_FILE).exists())

    def test_old_manifest_cannot_replace_newer_homepage_or_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publish(root, "2026-11-08")
            write = publisher._write_json_atomic

            def fail_index(path, value):
                if path.name == "index.json":
                    raise OSError("interrupted")
                write(path, value)

            with patch.object(publisher, "_write_json_atomic", side_effect=fail_index):
                with self.assertRaises(OSError):
                    publish(root, "2026-11-09")
            manifest = (root / publisher.TRANSACTION_FILE).read_bytes()
            publish(root, "2026-11-10")
            before = {name: (root / name).read_bytes() for name in ("index.json", "index.html", "history.json")}
            (root / publisher.TRANSACTION_FILE).write_bytes(manifest)
            publisher.recover_publication(root, now=datetime(2026, 11, 9, 8, 30, tzinfo=BEIJING))
            self.assertEqual({name: (root / name).read_bytes() for name in before}, before)

    def test_new_month_may_start_a_fresh_usage_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state").mkdir()
            (root / "state/initialized").touch()
            (root / "state/usage-month").write_text("2026-10")
            client = DashScopeClient(
                api_key="test",
                config=DashScopeConfig(max_retries=0),
                transport=lambda payload, _: classification_response(payload),
            )
            run_daily(
                root / "site",
                root / "state",
                clock=lambda: datetime(2026, 11, 1, 8, tzinfo=BEIJING),
                client=client,
                fetch=lambda _: (Path(__file__).parent / "fixtures/trending_page.html").read_text(encoding="utf-8"),
                source_loader=lambda _: {},
            )
            self.assertTrue((root / "state/artifacts/monthly-2026-11.json").exists())

    def test_daily_at_cutoff_rolls_back_pending_issue_without_external_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "site"
            (root / "state").mkdir()
            (root / "state/initialized").touch()
            publish(site, "2026-11-08")
            write = publisher._write_json_atomic

            def fail_history(path, value):
                if path.name == "history.json":
                    raise OSError("interrupted")
                write(path, value)

            with patch.object(publisher, "_write_json_atomic", side_effect=fail_history):
                with self.assertRaises(OSError):
                    publish(site, "2026-11-09")
            with self.assertRaises(TimeoutError):
                run_daily(
                    site,
                    root / "state",
                    clock=lambda: datetime(2026, 11, 9, 9, tzinfo=BEIJING),
                    fetch=lambda _: self.fail("expired collection"),
                )
            self.assertEqual(json.loads((site / "index.json").read_text())["latest_report_date"], "2026-11-08")
            self.assertFalse((site / publisher.TRANSACTION_FILE).exists())

    def test_interrupted_resume_can_be_abandoned_without_orphaned_new_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publish(root, "2026-11-08")
            originals = {name: (root / name).read_bytes() for name in ("index.json", "index.html", "history.json")}
            write = publisher._write_json_atomic

            def fail_history(path, value):
                if path.name == "history.json":
                    raise OSError("interrupted commit")
                write(path, value)

            with patch.object(publisher, "_write_json_atomic", side_effect=fail_history):
                with self.assertRaises(OSError):
                    publish(root, "2026-11-09")
                with self.assertRaises(OSError):
                    publisher.recover_publication(root, now=datetime(2026, 11, 9, 8, 30, tzinfo=BEIJING))
            self.assertEqual(
                json.loads((root / "archive/2026-11-09/report.json").read_text())["published_at"],
                "2026-11-09T00:30:00+00:00",
            )
            publisher.recover_publication(root, now=datetime(2026, 11, 9, 9, tzinfo=BEIJING))
            self.assertFalse((root / "archive/2026-11-09/report.json").exists())
            self.assertEqual({name: (root / name).read_bytes() for name in originals}, originals)

    def test_legacy_manifest_without_backups_stops_without_late_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = {
                "report": report("2026-11-09"),
                "html": "<main>pending</main>",
                "homepage": "pending",
                "index": {},
                "history": [],
            }
            (root / publisher.TRANSACTION_FILE).write_text(json.dumps(pending))
            with self.assertRaisesRegex(ValueError, "operator recovery"):
                publisher.recover_publication(root, now=datetime(2026, 11, 10, 8, tzinfo=BEIJING))
            self.assertFalse((root / "archive").exists())
            self.assertTrue((root / publisher.TRANSACTION_FILE).exists())
