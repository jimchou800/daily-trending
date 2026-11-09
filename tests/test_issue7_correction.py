import json
import tempfile
import unittest
from pathlib import Path

from daily_trending.publisher import correct_report, publish_report


class CorrectionTests(unittest.TestCase):
    def _published_site(self, root: Path, date: str = "2026-11-09") -> dict:
        report = {
            "report_date": date,
            "status": "sample_preview",
            "contract_version": "1.0",
            "timezone": "Asia/Shanghai",
            "collected_at": f"{date}T00:00:00Z",
            "entry_count": 1,
            "scope": "GitHub Trending Daily（全部编程语言）",
            "source": "https://github.com/trending?since=daily",
            "entries": [{"repository": "acme/project", "repository_url": "https://github.com/acme/project", "rank": 1, "heat": {"stars_today": 10, "stars_total": 100}, "sources": ["https://github.com/acme/project"], "ai_relevance_evidence": "AI tool", "project_type": "tool", "introduction": "Original", "problem_solved": "problem", "audience": "engineers", "characteristics": "feature", "reason_has_evidence": False}],
            "history": [{"repo": "acme/project", "period": "2026-11-01"}],
        }
        path = publish_report(
            report,
            "<main>old</main>",
            root,
            "2026-11-09T00:30:00Z",
            now=__import__("datetime").datetime.fromisoformat(f"{date}T08:00:00+08:00"),
        )
        return json.loads(path.read_text(encoding="utf-8"))

    def test_correction_preserves_identity_selection_and_publish_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = self._published_site(root)
            corrected = dict(original, summary="corrected fact")
            path = correct_report(
                corrected,
                root,
                "2026-11-09T02:00:00Z",
                "修正仓库说明",
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["report_date"], original["report_date"])
            self.assertEqual(saved["published_at"], "2026-11-09T00:30:00Z")
            self.assertEqual(saved["entries"], original["entries"])
            self.assertEqual(saved["history"], original["history"])
            self.assertEqual(saved["correction"], {"corrected_at": "2026-11-09T02:00:00Z", "note": "修正仓库说明"})
            self.assertIn("修正仓库说明", (root / "archive" / "2026-11-09" / "index.html").read_text())
            correct_report(corrected, root, "2026-11-09T02:30:00Z", "<script>alert(1)</script>")
            rendered = (root / "archive" / "2026-11-09" / "index.html").read_text()
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
            self.assertNotIn("<script>alert(1)</script>", rendered)

    def test_correction_rejects_changed_date_or_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = self._published_site(root)
            with self.assertRaisesRegex(ValueError, "published report"):
                correct_report(dict(original, report_date="2026-11-10"), root, "2026-11-09T02:00:00Z", "bad")
            with self.assertRaisesRegex(ValueError, "immutable"):
                correct_report(dict(original, entries=[]), root, "2026-11-09T02:00:00Z", "bad")

    def test_historical_correction_does_not_roll_back_newer_homepage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._published_site(root, "2026-11-08")
            self._published_site(root, "2026-11-09")
            index_before = (root / "index.json").read_text()
            homepage = root / "index.html"
            homepage.write_text("<main>newest</main>")
            old = json.loads((root / "archive" / "2026-11-08" / "report.json").read_text())
            correct_report(dict(old, entries=[dict(old["entries"][0], introduction="fixed")]), root, "2026-11-09T02:00:00Z", "历史修正")
            self.assertEqual((root / "index.json").read_text(), index_before)
            self.assertEqual(homepage.read_text(), "<main>newest</main>")
            self.assertEqual(json.loads((root / "archive" / "2026-11-09" / "report.json").read_text())["published_at"], "2026-11-09T00:30:00Z")

    def test_latest_correction_refreshes_homepage_and_failure_restores_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = self._published_site(root)
            homepage = root / "index.html"
            homepage.write_text("<main>old</main>")
            target = root / "archive" / "2026-11-09" / "report.json"
            page = target.with_name("index.html")
            original_json, original_html, original_home = target.read_bytes(), page.read_bytes(), homepage.read_bytes()
            import unittest.mock
            real_replace = __import__("os").replace
            calls = 0

            def fail_on_page(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated replace failure")
                return real_replace(source, destination)

            with unittest.mock.patch("daily_trending.publisher.os.replace", side_effect=fail_on_page):
                with self.assertRaisesRegex(OSError, "simulated"):
                    correct_report(dict(original, entries=[dict(original["entries"][0], introduction="new")]), root, "2026-11-09T02:00:00Z", "修正")
            self.assertEqual(target.read_bytes(), original_json)
            self.assertEqual(page.read_bytes(), original_html)
            self.assertEqual(homepage.read_bytes(), original_home)


if __name__ == "__main__":
    unittest.main()
