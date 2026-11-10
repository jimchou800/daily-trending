import unittest
import json
import tempfile
from pathlib import Path

from daily_trending.selection import filter_repeat_entries
from daily_trending.pipeline import run_sample_pipeline


def item(repository, rank, stars_today=10):
    return {
        "repository": repository,
        "rank": rank,
        "heat": {"stars_today": stars_today, "stars_total": 100},
        "sources": [],
    }


class RepeatSelectionTests(unittest.TestCase):
    def setUp(self):
        self.history = [
            {
                "report_date": "2026-11-01",
                "status": "published",
                "published_at": "2026-11-01T01:30:00Z",
                "entries": [item("acme/recent", 1, 10), item("acme/released", 2, 10)],
            },
            {"report_date": "2026-11-02", "status": "sample_preview", "entries": [item("acme/preview", 1)]},
            {"report_date": "2026-11-03", "status": "failed", "entries": [item("acme/failed", 1)]},
        ]

    def test_only_successfully_published_reports_create_history(self):
        candidates = [item("acme/preview", 1), item("acme/failed", 2)]
        selected, decisions = filter_repeat_entries(candidates, "2026-11-04", self.history)
        self.assertEqual([entry["repository"] for entry in selected], ["acme/preview", "acme/failed"])
        self.assertTrue(all(decision["eligible"] for decision in decisions))

    def test_window_uses_report_date_at_beijing_nine_and_includes_exact_168_hours(self):
        candidates = [item("acme/recent", 1), item("acme/after", 2)]
        history = self.history + [
            {"report_date": "2026-11-02", "status": "published", "entries": [item("acme/after", 1)]}
        ]
        selected, decisions = filter_repeat_entries(candidates, "2026-11-08", history)
        self.assertEqual([entry["repository"] for entry in selected], ["acme/recent"])
        self.assertEqual(decisions[0]["reason"], "window_elapsed")

    def test_recent_repeat_requires_new_formal_release_or_same_metric_star_doubling(self):
        candidates = [item("acme/recent", 1, 20), item("acme/released", 2, 11), item("acme/nochange", 3, 10)]
        history = [
            {
                "report_date": "2026-11-07",
                "status": "published",
                "entries": [item("acme/recent", 1, 10), item("acme/released", 2, 10), item("acme/nochange", 3, 10)],
            }
        ]
        releases = {
            "acme/released": [
                {
                    "tag_name": "v2",
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-11-08T00:00:00Z",
                    "html_url": "https://github.com/acme/released/releases/tag/v2",
                }
            ]
        }
        selected, decisions = filter_repeat_entries(candidates, "2026-11-08", history, releases_by_repository=releases)
        self.assertEqual([entry["repository"] for entry in selected], ["acme/recent", "acme/released"])
        self.assertEqual(decisions[0]["repeat_basis"], "stars_doubled")
        self.assertEqual(decisions[1]["repeat_basis"], "new_formal_release")
        self.assertEqual(selected[0]["previous_report_url"], "/archive/2026-11-07/")
        self.assertIn("20", selected[0]["repeat_change"])

    def test_elapsed_repeat_keeps_previous_link_and_reports_only_known_comparison(self):
        selected, _ = filter_repeat_entries([item("acme/recent", 1, None)], "2026-11-08", self.history)
        self.assertEqual(selected[0]["previous_report_url"], "/archive/2026-11-01/")
        self.assertEqual(selected[0]["repeat_basis"], "window_elapsed")
        self.assertIn("未获取", selected[0]["repeat_change"])
        self.assertNotIn("增长", selected[0]["repeat_change"])

    def test_release_repeat_describes_verified_release_even_when_stars_unchanged(self):
        release = {
            "tag_name": "v2",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-11-03T00:00:00Z",
            "html_url": "https://github.com/acme/released/releases/tag/v2",
        }
        selected, decisions = filter_repeat_entries(
            [item("acme/released", 1)], "2026-11-04", self.history, releases_by_repository={"acme/released": [release]}
        )
        self.assertIn("v2", selected[0]["repeat_change"])
        self.assertIn(release["published_at"], selected[0]["repeat_change"])
        self.assertIn(release["html_url"], selected[0]["sources"])
        self.assertEqual(decisions[0]["release"], release)

    def test_missing_or_zero_previous_metric_never_qualifies_for_star_exception(self):
        candidates = [item("acme/missing", 1, 100), item("acme/zero", 2, 100)]
        history = [
            {
                "report_date": "2026-11-07",
                "status": "published",
                "entries": [item("acme/missing", 1, None), item("acme/zero", 2, 0)],
            }
        ]
        selected, decisions = filter_repeat_entries(candidates, "2026-11-08", history)
        self.assertEqual(selected, [])
        self.assertEqual([decision["reason"] for decision in decisions], ["recent_duplicate", "recent_duplicate"])

    def test_release_exception_requires_identifiable_repository_release_source(self):
        release = {"tag_name": "v2", "draft": False, "prerelease": False, "published_at": "2026-11-03T00:00:00Z"}
        for url in (None, "https://example.com/release", "https://github.com/wrong/repo/releases/tag/v2"):
            selected, _ = filter_repeat_entries(
                [item("acme/released", 1)],
                "2026-11-04",
                self.history,
                releases_by_repository={"acme/released": [dict(release, html_url=url)]},
            )
            self.assertEqual(selected, [])

    def test_only_latest_successful_occurrence_is_used_and_order_and_limit_are_preserved(self):
        history = [
            {"report_date": "2026-11-01", "status": "published", "entries": [item("acme/repo", 1, 10)]},
            {"report_date": "2026-11-07", "status": "published", "entries": [item("acme/repo", 1, 12)]},
        ]
        candidates = [item(f"acme/{i}", i) for i in range(1, 8)]
        candidates[0] = item("acme/repo", 1, 12)
        selected, _ = filter_repeat_entries(candidates, "2026-11-08", history)
        self.assertEqual([entry["rank"] for entry in selected], [2, 3, 4, 5, 6])

    def test_sample_pipeline_filters_before_report_render_and_keeps_decisions_internal(self):
        history = [{"report_date": "2026-11-07", "status": "published", "entries": [item("example/alpha", 1, 321)]}]
        fixtures = Path(__file__).parent / "fixtures"
        with tempfile.TemporaryDirectory() as directory:
            result = run_sample_pipeline(
                fixtures / "trending_snapshot.json",
                fixtures / "model_responses.json",
                Path(directory),
                "2026-11-08",
                "2026-11-08T00:00:00Z",
                history=history,
            )
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            decisions = json.loads((Path(directory) / "selection.json").read_text(encoding="utf-8"))
            self.assertNotIn("example/alpha", [entry["repository"] for entry in report["entries"]])
            self.assertIn("recent_duplicate", [decision["reason"] for decision in decisions])
            self.assertNotIn("recent_duplicate", result.html_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
