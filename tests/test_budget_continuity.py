import json
import tempfile
import unittest
from pathlib import Path

from daily_trending.dashscope import DashScopeClient, DashScopeConfig
from daily_trending.pipeline import run_live_pipeline


class BudgetContinuityTests(unittest.TestCase):
    def test_next_period_has_own_call_allowance_but_shared_monthly_spend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def transport(payload, _key):
                calls.append(payload)
                repository = json.loads(payload["messages"][0]["content"].split("\n", 1)[1])[0]["repository"]
                return {"choices": [{"message": {"content": json.dumps({"repository": repository, "is_ai": False})}}]}

            config = DashScopeConfig(max_calls=2, max_retries=0, estimated_call_cost_cny=0.1, monthly_budget_cny=0.4)
            for period in ("2026-11-09", "2026-11-10"):
                run_live_pipeline(
                    root / period,
                    period,
                    f"{period}T00:30:00Z",
                    DashScopeClient(api_key="test", config=config, transport=transport),
                    fetch=lambda _: (Path(__file__).parent / "fixtures/trending_page.html").read_text(encoding="utf-8"),
                    source_loader=lambda _: {"readme": "README"},
                    artifact_root=root / "artifacts",
                )
            self.assertEqual(len(calls), 4)
            ledger = json.loads((root / "artifacts/monthly-2026-11.json").read_text())
            self.assertEqual(ledger["calls_used"], 4)
            self.assertAlmostEqual(ledger["estimated_spend_cny"], 0.4)

    def test_missing_monthly_ledger_with_existing_period_state_stops_before_network(self):
        from daily_trending.persistence import ArtifactStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = ArtifactStore(root / "artifacts", "2026-11-09")
            previous.update_usage(2, 29.95)
            (root / "artifacts/monthly-2026-11.json").unlink()
            calls = []
            with self.assertRaisesRegex(ValueError, "monthly usage ledger"):
                run_live_pipeline(
                    root / "output",
                    "2026-11-10",
                    "2026-11-10T00:30:00Z",
                    DashScopeClient(api_key="test", transport=lambda *_: calls.append("paid")),
                    fetch=lambda _: (Path(__file__).parent / "fixtures/trending_page.html").read_text(encoding="utf-8"),
                    source_loader=lambda _: {"readme": "README"},
                    artifact_root=root / "artifacts",
                )
            self.assertEqual(calls, [])

    def test_invalid_monthly_usage_is_rejected_instead_of_reset_or_unlimited_budget(self):
        from daily_trending.persistence import ArtifactStore

        invalid_ledgers = [
            [],
            None,
            1,
            {},
            {"calls_used": 1, "estimated_spend_cny": float("nan")},
            {"calls_used": 1, "estimated_spend_cny": float("inf")},
            {"calls_used": True, "estimated_spend_cny": 0.1},
            {"calls_used": 1.5, "estimated_spend_cny": 0.1},
            {"calls_used": 1, "estimated_spend_cny": "0.1"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root, "2026-11-10")
            for data in invalid_ledgers:
                with self.subTest(data=data):
                    (root / "monthly-2026-11.json").write_text(json.dumps(data))
                    with self.assertRaisesRegex(ValueError, "monthly usage ledger is corrupt"):
                        store.monthly_usage()

    def test_monthly_reservation_survives_period_state_write_failure(self):
        from unittest.mock import patch
        import os
        from daily_trending.persistence import ArtifactStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root, "2026-11-09")
            store.update_usage(0, 0)
            replace = os.replace

            def interrupted_replace(source, destination):
                if Path(destination).name == "run-state.json":
                    raise OSError("period state write interrupted")
                return replace(source, destination)

            with patch("daily_trending.persistence.os.replace", side_effect=interrupted_replace):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    store.update_usage(1, 0.1)
            restarted = ArtifactStore(root, "2026-11-10")
            self.assertEqual(restarted.monthly_usage(), {"calls_used": 1, "estimated_spend_cny": 0.1})

    def test_previous_month_exhaustion_does_not_exhaust_next_month(self):
        from daily_trending.persistence import ArtifactStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ArtifactStore(root, "2026-11-30").update_usage(600, 30.0)
            next_month = ArtifactStore(root, "2026-12-01")
            self.assertEqual(next_month.monthly_usage(), {"calls_used": 0, "estimated_spend_cny": 0.0})

    def test_deadline_guard_prevents_retry_reservation_and_network(self):
        calls = []
        expired = False

        def guard():
            if expired:
                raise RuntimeError("deadline expired")

        def transport(*_):
            nonlocal expired
            calls.append("request")
            expired = True
            raise ConnectionError("retryable network failure")

        client = DashScopeClient(
            api_key="test",
            transport=transport,
            config=DashScopeConfig(max_retries=2),
            before_call=guard,
        )
        with self.assertRaisesRegex(RuntimeError, "deadline expired"):
            client.complete("source")
        self.assertEqual(calls, ["request"])
        self.assertEqual(client.calls_used, 1)
        self.assertAlmostEqual(client.estimated_spend_cny, 0.068304)

    def test_reused_client_and_monthly_exhaustion_cannot_reset_or_double_charge_usage(self):
        from daily_trending.persistence import ArtifactStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def transport(payload, _key):
                calls.append(payload)
                repository = json.loads(payload["messages"][0]["content"].split("\n", 1)[1])[0]["repository"]
                return {"choices": [{"message": {"content": json.dumps({"repository": repository, "is_ai": False})}}]}

            client = DashScopeClient(
                api_key="test",
                transport=transport,
                config=DashScopeConfig(max_calls=2, max_retries=0, estimated_call_cost_cny=0.1, monthly_budget_cny=0.4),
            )
            for period in ("2026-11-09", "2026-11-10"):
                run_live_pipeline(
                    root / period,
                    period,
                    f"{period}T00:30:00Z",
                    client,
                    fetch=lambda _: (Path(__file__).parent / "fixtures/trending_page.html").read_text(encoding="utf-8"),
                    source_loader=lambda _: {"readme": "README"},
                    artifact_root=root / "artifacts",
                )
            self.assertEqual(len(calls), 4)
            first_state = ArtifactStore(root / "artifacts", "2026-11-09").state
            second = ArtifactStore(root / "artifacts", "2026-11-10")
            self.assertAlmostEqual(first_state["estimated_spend_cny"], 0.2)
            self.assertAlmostEqual(second.state["estimated_spend_cny"], 0.2)
            self.assertEqual(second.monthly_usage()["calls_used"], 4)
            with self.assertRaises(ValueError):
                run_live_pipeline(
                    root / "blocked",
                    "2026-11-11",
                    "2026-11-11T00:30:00Z",
                    client,
                    fetch=lambda _: (Path(__file__).parent / "fixtures/trending_page.html").read_text(encoding="utf-8"),
                    source_loader=lambda _: {"readme": "README"},
                    artifact_root=root / "artifacts",
                )
            self.assertEqual(len(calls), 4)
            self.assertEqual(second.monthly_usage()["estimated_spend_cny"], 0.4)
