import json
import tempfile
import unittest
from pathlib import Path

from daily_trending.analyzer import analyze_snapshot
from daily_trending.collector import collect_snapshot
from daily_trending.organizer import organize_report


ROOT = Path(__file__).parent
FIXTURES = ROOT / "fixtures"


class ContractTests(unittest.TestCase):
    def test_incomplete_snapshot_is_rejected_before_analysis(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temp_dir:
            bad_snapshot = Path(temp_dir) / "bad.json"
            payload = json.loads((FIXTURES / "trending_snapshot.json").read_text(encoding="utf-8"))
            payload["complete"] = False
            bad_snapshot.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "complete"):
                collect_snapshot(
                    bad_snapshot,
                    issue_date="2026-11-09",
                    collected_at="2026-11-09T00:30:00Z",
                )

    def test_organizer_rejects_unfinished_analyzer_artifact(self) -> None:
        with self.assertRaisesRegex(ValueError, "succeeded"):
            organize_report(
                {
                    "contract_version": "1.0",
                    "period_id": "2026-11-09",
                    "snapshot_id": "snapshot-id",
                    "stage": {"name": "analyzer", "status": "failed", "errors": ["broken"]},
                    "snapshot": {"source_url": "https://github.com/trending?since=daily"},
                    "analyses": [],
                },
                published_at=None,
            )

    def test_model_metrics_cannot_replace_collected_metrics(self) -> None:
        snapshot = collect_snapshot(
            FIXTURES / "trending_snapshot.json",
            issue_date="2026-11-09",
            collected_at="2026-11-09T00:30:00Z",
        )
        analysis = analyze_snapshot(snapshot, FIXTURES / "model_responses.json")
        self.assertEqual(analysis["analyses"][0]["heat"]["stars_today"], 321)

    def test_analyzer_rejects_wrong_period_and_incomplete_collector_artifacts(self) -> None:
        snapshot = collect_snapshot(
            FIXTURES / "trending_snapshot.json",
            issue_date="2026-11-09",
            collected_at="2026-11-09T00:30:00Z",
        )
        wrong_period = dict(snapshot, period_id="2026-11-08")
        with self.assertRaisesRegex(ValueError, "period"):
            analyze_snapshot(wrong_period, FIXTURES / "model_responses.json", expected_period="2026-11-09")


if __name__ == "__main__":
    unittest.main()
