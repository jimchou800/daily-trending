import json
import tempfile
import unittest
from pathlib import Path
from daily_trending.dashscope import DashScopeClient, DashScopeConfig
from daily_trending.persistence import ArtifactStore
from daily_trending.pipeline import run_live_pipeline


class AccountingPersistenceTests(unittest.TestCase):
    def test_usage_mismatch_blocks_paid_work_after_restart_and_next_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def transport(*_):
                calls.append("paid")
                return {
                    "usage": {"prompt_tokens": 1, "completion_tokens": 10000, "total_tokens": 10001},
                    "choices": [{"message": {"content": "{}"}}],
                }

            for date in ("2026-11-09", "2026-11-09", "2026-11-10"):
                client = DashScopeClient(api_key="test", transport=transport, config=DashScopeConfig(max_retries=0))
                with self.assertRaises(ValueError):
                    run_live_pipeline(
                        root / ("output" + date),
                        date,
                        date + "T00:30:00Z",
                        client,
                        fetch=lambda _: (Path(__file__).parent / "fixtures/trending_page.html").read_text(
                            encoding="utf-8"
                        ),
                        source_loader=lambda _: {"readme": "AI"},
                        artifact_root=root / "state",
                    )
            self.assertEqual(calls, ["paid"])
            usage = ArtifactStore(root / "state", "2026-11-10").monthly_usage()
            self.assertTrue(usage["accounting_blocked"])
            self.assertEqual(len(usage["usage_records"]), 1)
            self.assertEqual(usage["calls_used"], 1)

    def test_malformed_accounting_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root, "2026-11-09")
            for invalid in ({"accounting_blocked": "false"}, {"usage_records": "paid"}, {"usage_records": [None]}):
                (root / "monthly-2026-11.json").write_text(
                    json.dumps(dict(calls_used=0, estimated_spend_cny=0, **invalid)), encoding="utf-8"
                )
                with self.assertRaisesRegex(ValueError, "corrupt"):
                    store.monthly_usage()
