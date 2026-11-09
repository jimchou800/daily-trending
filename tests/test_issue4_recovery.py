import json
import re
import tempfile
import unittest
from pathlib import Path

from daily_trending.dashscope import DashScopeClient, DashScopeConfig
from daily_trending.pipeline import run_live_pipeline
from daily_trending.persistence import ArtifactStore


class Issue4RecoveryTests(unittest.TestCase):
    def test_failed_pipeline_persists_failure_and_retry_count(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            calls = []

            def fetch(_url: str) -> str:
                calls.append("fetch")
                raise OSError("network down")

            client = DashScopeClient(api_key="key", config=DashScopeConfig(max_retries=0))
            with self.assertRaises(OSError):
                run_live_pipeline(root / "output", "2026-11-09", "2026-11-09T00:30:00Z", client, fetch=fetch, artifact_root=root / "artifacts")
            state = json.loads((root / "artifacts" / "2026-11-09" / "run-state.json").read_text())
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["run_count"], 1)
            self.assertTrue(state["failures"])
            self.assertEqual(calls, ["fetch"])

    def test_corrupt_analyzer_artifact_is_ignored_and_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            calls = []

            def fetch(_url: str) -> str:
                return (Path(__file__).parent / "fixtures" / "trending_page.html").read_text(encoding="utf-8")

            def transport(payload, _key):
                calls.append(payload)
                repository = json.loads(payload["messages"][0]["content"].split("\n", 1)[1])[0]["repository"]
                return {"choices": [{"message": {"content": json.dumps({
                    "repository": repository, "is_ai": True,
                    "ai_relevance_evidence": "README", "project_type": "tool", "introduction": "intro",
                    "problem_solved": "problem", "audience": "developers", "characteristics": "feature",
                    "reason_has_evidence": False, "sources": [f"https://github.com/{repository}"],
                })}}]}

            config = DashScopeConfig(max_retries=0)
            run_live_pipeline(root / "output", "2026-11-09", "2026-11-09T00:30:00Z", DashScopeClient(api_key="key", config=config, transport=transport), fetch=fetch, source_loader=lambda _: {"readme": "README"}, artifact_root=root / "artifacts")
            first_call_count = len(calls)
            analyzer = root / "artifacts" / "2026-11-09" / "analyzer.json"
            analyzer.write_text("{broken", encoding="utf-8")
            run_live_pipeline(root / "output2", "2026-11-09", "2026-11-09T00:30:00Z", DashScopeClient(api_key="key", config=config, transport=transport), fetch=lambda _: (_ for _ in ()).throw(AssertionError("snapshot must be reused")), source_loader=lambda _: {"readme": "README"}, artifact_root=root / "artifacts")
            self.assertGreater(first_call_count, 0)
            self.assertEqual(len(calls), first_call_count)

    def test_cache_model_mismatch_does_not_reuse_response(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            store = ArtifactStore(Path(directory), "2026-11-09")
            store.save_analysis("example/alpha", "snapshot", "model-a", {"repository": "example/alpha", "is_ai": True})
            self.assertIsNone(store.load_analysis("example/alpha", "snapshot", "model-b"))


if __name__ == "__main__":
    unittest.main()
