import json
import tempfile
import unittest
from pathlib import Path

from daily_trending.pipeline import run_sample_pipeline


ROOT = Path(__file__).parent
FIXTURES = ROOT / "fixtures"


class PipelineIntegrationTests(unittest.TestCase):
    def test_saved_samples_generate_valid_report_and_responsive_page(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temp_dir:
            result = run_sample_pipeline(
                FIXTURES / "trending_snapshot.json",
                FIXTURES / "model_responses.json",
                Path(temp_dir),
                issue_date="2026-11-09",
                collected_at="2026-11-09T00:30:00Z",
            )

            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "sample_preview")
            self.assertIsNone(report["published_at"])
            self.assertEqual(report["report_date"], "2026-11-09")
            self.assertEqual(report["scope"], "GitHub Trending Daily（全部编程语言）")
            self.assertEqual(report["entry_count"], 2)
            self.assertEqual([entry["rank"] for entry in report["entries"]], [1, 3])
            self.assertEqual(report["entries"][0]["heat"]["stars_today"], 321)
            self.assertIsNone(report["entries"][1]["heat"]["stars_total"])

            html = result.html_path.read_text(encoding="utf-8")
            self.assertIn("viewport", html)
            self.assertIn("未获取", html)
            self.assertIn("暂未找到明确原因", html)
            self.assertIn("https://github.com/example/alpha", html)
            self.assertNotIn('published_at">2026', html)

    def test_pipeline_rejects_wrong_snapshot_link_between_stages(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temp_dir:
            with self.assertRaisesRegex(ValueError, "snapshot"):
                run_sample_pipeline(
                    FIXTURES / "trending_snapshot.json",
                    FIXTURES / "model_responses_wrong_snapshot.json",
                    Path(temp_dir),
                    issue_date="2026-11-09",
                    collected_at="2026-11-09T00:30:00Z",
                )


if __name__ == "__main__":
    unittest.main()
