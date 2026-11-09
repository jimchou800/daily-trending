import unittest

from daily_trending.selection import filter_repeat_entries


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
        history = [{"report_date": "2026-11-07", "status": "published", "entries": [item("acme/recent", 1, 10), item("acme/released", 2, 10), item("acme/nochange", 3, 10)]}]
        releases = {"acme/released": [{"tag_name": "v2", "draft": False, "prerelease": False, "published_at": "2026-11-08T00:00:00Z"}]}
        selected, decisions = filter_repeat_entries(candidates, "2026-11-08", history, releases_by_repository=releases)
        self.assertEqual([entry["repository"] for entry in selected], ["acme/recent", "acme/released"])
        self.assertEqual(decisions[0]["repeat_basis"], "stars_doubled")
        self.assertEqual(decisions[1]["repeat_basis"], "new_formal_release")
        self.assertEqual(selected[0]["previous_report_url"], "archive/2026-11-07/")
        self.assertIn("20", selected[0]["repeat_change"])

    def test_missing_or_zero_previous_metric_never_qualifies_for_star_exception(self):
        candidates = [item("acme/missing", 1, 100), item("acme/zero", 2, 100)]
        history = [{"report_date": "2026-11-07", "status": "published", "entries": [item("acme/missing", 1, None), item("acme/zero", 2, 0)]}]
        selected, decisions = filter_repeat_entries(candidates, "2026-11-08", history)
        self.assertEqual(selected, [])
        self.assertEqual([decision["reason"] for decision in decisions], ["recent_duplicate", "recent_duplicate"])

    def test_only_latest_successful_occurrence_is_used_and_order_and_limit_are_preserved(self):
        history = [
            {"report_date": "2026-11-01", "status": "published", "entries": [item("acme/repo", 1, 10)]},
            {"report_date": "2026-11-07", "status": "published", "entries": [item("acme/repo", 1, 12)]},
        ]
        candidates = [item(f"acme/{i}", i) for i in range(1, 8)]
        candidates[0] = item("acme/repo", 1, 12)
        selected, _ = filter_repeat_entries(candidates, "2026-11-08", history)
        self.assertEqual([entry["rank"] for entry in selected], [2, 3, 4, 5, 6])


if __name__ == "__main__":
    unittest.main()
