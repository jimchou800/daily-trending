from tests.model_stub import classification_response
import json
import tempfile
import unittest
from pathlib import Path

from daily_trending.persistence import ArtifactStore, MAX_RETRIES
from daily_trending.analyzer import analyze_live_snapshot
from daily_trending.collector import collect_snapshot
from daily_trending.dashscope import DashScopeClient, DashScopeConfig


class PersistenceTests(unittest.TestCase):
    def test_interrupted_or_failed_runs_consume_only_two_resumptions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root, "2026-11-09")
            store.begin_run()
            store.finish_run("failed", "collector timeout")
            ArtifactStore(root, "2026-11-09").begin_run()
            store = ArtifactStore(root, "2026-11-09")
            store.finish_run("failed", "model timeout")
            ArtifactStore(root, "2026-11-09").begin_run()
            with self.assertRaises(RuntimeError):
                ArtifactStore(root, "2026-11-09").begin_run()

    def test_snapshot_is_reused_and_corrupt_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory), "2026-11-09")
            snapshot = {
                "contract_version": "1.0",
                "period_id": "2026-11-09",
                "snapshot_id": "abc",
                "stage": {"name": "collector", "status": "succeeded"},
            }
            store.save("collector", snapshot)
            self.assertEqual(store.load_compatible("collector", "abc")["snapshot_id"], "abc")
            (Path(directory) / "2026-11-09" / "collector.json").write_text("{bad", encoding="utf-8")
            self.assertIsNone(store.load_compatible("collector", "abc"))

    def test_retry_and_cost_state_survive_reopen_and_cap_at_two(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root, "2026-11-09")
            store.record_attempt("failed", "network", cost_cny=0.05)
            store.record_attempt("failed", "timeout", cost_cny=0.05)
            reopened = ArtifactStore(root, "2026-11-09")
            self.assertEqual(reopened.state["attempts"], 2)
            self.assertEqual(reopened.state["estimated_spend_cny"], 0.1)
            with self.assertRaises(RuntimeError):
                reopened.record_attempt("failed", "third")
            self.assertEqual(MAX_RETRIES, 2)

    def test_live_pipeline_reuses_successful_candidate_analysis_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(
                json.dumps(
                    {
                        "complete": True,
                        "entries": [
                            {
                                "rank": 1,
                                "repository": "acme/one",
                                "repository_url": "https://github.com/acme/one",
                                "stars_today": 4,
                                "stars_total": 20,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            snapshot = collect_snapshot(source, "2026-11-09", "2026-11-09T00:30:00Z")
            calls = []

            def transport(payload, api_key):
                calls.append(payload)
                if "classifications" in payload["messages"][0]["content"].split("\n", 1)[0]:
                    return classification_response(payload, is_ai=True, evidence="README")
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "repository": "acme/one",
                                        "is_ai": True,
                                        "ai_relevance_evidence": "README",
                                        "project_type": "tool",
                                        "introduction": "intro",
                                        "problem_solved": "problem",
                                        "audience": "developers",
                                        "characteristics": "simple",
                                        "reason_has_evidence": False,
                                        "sources": ["https://github.com/acme/one"],
                                    }
                                )
                            }
                        }
                    ]
                }

            store = ArtifactStore(root / "artifacts", snapshot["period_id"])
            for _ in range(2):
                client = DashScopeClient(api_key="test", transport=transport, config=DashScopeConfig(max_retries=0))
                result = analyze_live_snapshot(snapshot, client, lambda entry: {"readme": "README"}, cache_store=store)
                self.assertEqual(result["analyses"][0]["repository"], "acme/one")
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
