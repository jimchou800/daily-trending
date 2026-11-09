import json
import tempfile
import unittest
from pathlib import Path

from daily_trending.publisher import load_successful_history, publish_report


class PublishTests(unittest.TestCase):
    def test_incomplete_report_is_rejected_before_any_publication_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "missing report field"):
                publish_report({"report_date": "2026-11-09", "status": "published"}, "", root, "2026-11-09T00:30:00Z")
            self.assertFalse((root / "archive").exists())

    def test_idempotent_archive_preserves_first_publish_time(self):
        report = {
            "report_date": "2026-11-09",
            "status": "sample_preview",
            "contract_version": "1.0",
            "timezone": "Asia/Shanghai",
            "collected_at": "2026-11-09T00:00:00Z",
            "source": "https://github.com/trending?since=daily",
            "scope": "GitHub Trending Daily（全部编程语言）",
            "entry_count": 0,
            "entries": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = publish_report(
                report,
                "<main>ok</main>",
                Path(directory),
                "2026-11-09T00:30:00Z",
                now=__import__("datetime").datetime.fromisoformat("2026-11-09T08:00:00+08:00"),
            )
            publish_report(
                report,
                "<main>ok</main>",
                Path(directory),
                "2026-11-09T01:00:00Z",
                now=__import__("datetime").datetime.fromisoformat("2026-11-09T08:30:00+08:00"),
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["published_at"], "2026-11-09T00:30:00Z")
            self.assertEqual(json.loads((Path(directory) / "index.json").read_text())["archive_dates"], ["2026-11-09"])
            homepage = (Path(directory) / "index.html").read_text(encoding="utf-8")
            self.assertIn("Asia/Shanghai", homepage)

    def test_late_publication_is_rejected_before_writing(self):
        report = {
            "report_date": "2026-11-09",
            "status": "published",
            "contract_version": "1.0",
            "timezone": "Asia/Shanghai",
            "collected_at": "2026-11-09T00:00:00Z",
            "source": "https://github.com/trending?since=daily",
            "scope": "GitHub Trending Daily（全部编程语言）",
            "entry_count": 0,
            "entries": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                publish_report(
                    report,
                    "",
                    Path(directory),
                    "2026-11-09T01:05:00Z",
                    now=__import__("datetime").datetime.fromisoformat("2026-11-09T09:05:00+08:00"),
                )
            self.assertFalse((Path(directory) / "archive").exists())

    def test_successful_history_is_persisted_once_and_recovery_completes(self):
        report = {
            "report_date": "2026-11-09",
            "status": "sample_preview",
            "contract_version": "1.0",
            "timezone": "Asia/Shanghai",
            "collected_at": "2026-11-09T00:00:00Z",
            "source": "https://github.com/trending?since=daily",
            "scope": "GitHub Trending Daily（全部编程语言）",
            "entry_count": 0,
            "entries": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            import unittest.mock

            real_replace = __import__("os").replace
            calls = 0

            def fail_once(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("interrupted publication")
                return real_replace(source, destination)

            with unittest.mock.patch("daily_trending.publisher.os.replace", side_effect=fail_once):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    publish_report(
                        report,
                        "<main>ok</main>",
                        root,
                        "2026-11-09T00:30:00Z",
                        now=__import__("datetime").datetime.fromisoformat("2026-11-09T08:00:00+08:00"),
                    )
            self.assertTrue((root / ".publication-transaction.json").exists())
            publish_report(
                report,
                "<main>ok</main>",
                root,
                "2026-11-09T01:00:00Z",
                now=__import__("datetime").datetime.fromisoformat("2026-11-09T08:30:00+08:00"),
            )
            self.assertFalse((root / ".publication-transaction.json").exists())
            self.assertEqual([item["report_date"] for item in load_successful_history(root)], ["2026-11-09"])
            self.assertEqual(len(json.loads((root / "history.json").read_text(encoding="utf-8"))), 1)


if __name__ == "__main__":
    unittest.main()
