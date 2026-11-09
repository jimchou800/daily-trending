import json
import tempfile
import unittest
from pathlib import Path

from daily_trending.publisher import publish_report


class PublishTests(unittest.TestCase):
    def test_idempotent_archive_preserves_first_publish_time(self):
        report = {"report_date": "2026-11-09", "status": "sample_preview", "html": "<main>ok</main>"}
        with tempfile.TemporaryDirectory() as directory:
            path = publish_report(report, "<main>ok</main>", Path(directory), "2026-11-09T00:30:00Z", now=__import__("datetime").datetime.fromisoformat("2026-11-09T08:00:00+08:00"))
            publish_report(report, "<main>ok</main>", Path(directory), "2026-11-09T01:00:00Z", now=__import__("datetime").datetime.fromisoformat("2026-11-09T08:30:00+08:00"))
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["published_at"], "2026-11-09T00:30:00Z")
            self.assertEqual(json.loads((Path(directory) / "index.json").read_text())["archive_dates"], ["2026-11-09"])
            homepage = (Path(directory) / "index.html").read_text(encoding="utf-8")
            self.assertIn("Asia/Shanghai", homepage)

    def test_late_publication_is_rejected_before_writing(self):
        report = {"report_date": "2026-11-09", "status": "published"}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                publish_report(report, "", Path(directory), "2026-11-09T01:05:00Z", now=__import__("datetime").datetime.fromisoformat("2026-11-09T09:05:00+08:00"))
            self.assertFalse((Path(directory) / "archive").exists())


if __name__ == "__main__":
    unittest.main()
