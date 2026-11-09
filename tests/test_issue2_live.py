import json
import unittest
from pathlib import Path

from daily_trending.collector import parse_trending_html
from daily_trending.analyzer import analyze_live_snapshot
from daily_trending.collector import collect_snapshot
from daily_trending.github_sources import load_github_sources
from daily_trending.pipeline import run_live_pipeline
from daily_trending.dashscope import (
    BudgetExceeded,
    DashScopeClient,
    DashScopeConfig,
    ModelCallError,
    build_analysis_prompt,
)


TRENDING_HTML = (Path(__file__).parent / "fixtures" / "trending_page.html").read_text(encoding="utf-8")


class LiveCollectorTests(unittest.TestCase):
    def test_parser_preserves_all_source_order_and_missing_metrics(self):
        entries = parse_trending_html(TRENDING_HTML)
        self.assertEqual([entry["rank"] for entry in entries], [1, 2])
        self.assertEqual([entry["repository"] for entry in entries], ["acme/first", "acme/second"])
        self.assertEqual(entries[0]["stars_total"], 1234)
        self.assertEqual(entries[0]["stars_today"], 56)
        self.assertIsNone(entries[1]["stars_total"])
        self.assertIsNone(entries[1]["stars_today"])

    def test_parser_rejects_a_page_without_complete_entries(self):
        with self.assertRaisesRegex(ValueError, "complete"):
            parse_trending_html("<html><body><h1>Sign in</h1></body></html>")

    def test_github_source_loader_excludes_draft_and_prerelease(self):
        urls = []

        def fetch(url):
            urls.append(url)
            if url.endswith("/readme"):
                import base64
                return {"content": base64.b64encode(b"# README").decode("ascii")}
            if "/releases" in url:
                return [
                    {"tag_name": "v1", "published_at": "2026-01-01", "html_url": "https://github.com/acme/first/releases/v1", "draft": False, "prerelease": False},
                    {"tag_name": "v2", "draft": False, "prerelease": True},
                ]
            return {"full_name": "acme/first", "description": "AI"}

        result = load_github_sources({"repository": "acme/first"}, fetch)
        self.assertEqual(result["readme"], "# README")
        self.assertEqual([release["tag_name"] for release in result["releases"]], ["v1"])
        self.assertTrue(all(url.startswith("https://api.github.com/repos/acme/first") for url in urls))


class DashScopeTests(unittest.TestCase):
    def test_analysis_prompt_contains_only_github_source_material(self):
        prompt = build_analysis_prompt([{"repository": "acme/first", "repository_url": "https://github.com/acme/first", "github_sources": {"readme": "# AI", "metadata": {"description": "AI"}}}])
        self.assertIn("# AI", prompt)
        self.assertNotIn("http://external.example", prompt)

    def test_client_retries_transient_failure_and_returns_json_content(self):
        calls = []

        def transport(payload, api_key):
            calls.append((payload, api_key))
            if len(calls) == 1:
                raise ConnectionError("temporary")
            return {"choices": [{"message": {"content": '{"is_ai": false}'}}]}

        client = DashScopeClient(
            api_key="secret",
            transport=transport,
            config=DashScopeConfig(max_retries=1, max_calls=2, estimated_call_cost_cny=0.1),
        )
        result = client.complete("source text")
        self.assertEqual(json.loads(result), {"is_ai": False})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0]["model"], "dashscope/qwen3.8-max")

    def test_client_stops_before_call_when_budget_is_exhausted(self):
        calls = []

        def transport(payload, api_key):
            calls.append(payload)
            return {"choices": [{"message": {"content": "{}"}}]}

        client = DashScopeClient(
            api_key="secret",
            transport=transport,
            config=DashScopeConfig(monthly_budget_cny=0.1, estimated_call_cost_cny=0.1),
        )
        client.complete("first")
        with self.assertRaises(BudgetExceeded):
            client.complete("second")
        self.assertEqual(len(calls), 1)

    def test_client_classifies_non_json_response_as_model_failure(self):
        def transport(payload, api_key):
            return {"choices": [{"message": {"content": "not json"}}]}

        client = DashScopeClient(api_key="secret", transport=transport)
        with self.assertRaises(ModelCallError):
            client.complete("source text")

    def test_live_analyzer_continues_after_one_candidate_failure(self):
        snapshot = collect_snapshot_from_entries()
        calls = []

        def transport(payload, api_key):
            calls.append(payload)
            repository = json.loads(payload["messages"][0]["content"].split("\n", 1)[1])[0]["repository"]
            if repository == "acme/first":
                return {"choices": [{"message": {"content": "not json"}}]}
            return {"choices": [{"message": {"content": json.dumps({"is_ai": False})}}]}

        client = DashScopeClient(api_key="secret", transport=transport, config=DashScopeConfig(max_retries=0))
        result = analyze_live_snapshot(snapshot, client, lambda entry: {"repository_url": entry["repository_url"]})
        self.assertEqual(result["stage"]["status"], "succeeded")
        self.assertEqual(result["analyses"], [])
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("analysis failed" in error for error in result["stage"]["errors"]))

    def test_live_pipeline_writes_preview_from_saved_html_and_model_transport(self):
        from tempfile import TemporaryDirectory

        def transport(payload, api_key):
            repository = json.loads(payload["messages"][0]["content"].split("\n", 1)[1])[0]["repository"]
            response = {"repository": repository, "is_ai": repository == "acme/first"}
            if response["is_ai"]:
                response.update({
                    "ai_relevance_evidence": "README describes an AI tool.",
                    "project_type": "工具",
                    "introduction": "用于处理 AI 工作流。",
                    "problem_solved": "减少重复操作。",
                    "audience": "开发者",
                    "characteristics": "提供可复用流程。",
                    "reason_has_evidence": False,
                    "sources": ["https://github.com/acme/first"],
                })
            return {"choices": [{"message": {"content": json.dumps(response)}}]}

        client = DashScopeClient(api_key="secret", transport=transport, config=DashScopeConfig(max_retries=0))
        with TemporaryDirectory() as directory:
            result = run_live_pipeline(
                Path(directory),
                "2026-11-09",
                "2026-11-09T00:30:00Z",
                client,
                fetch=lambda url: TRENDING_HTML,
                source_loader=lambda entry: {"repository_url": entry["repository_url"]},
            )
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "sample_preview")
            self.assertEqual(report["entry_count"], 1)
            self.assertEqual(report["entries"][0]["heat"]["stars_today"], 56)


def collect_snapshot_from_entries():
    from pathlib import Path
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        path = Path(directory) / "snapshot.json"
        path.write_text(json.dumps({"complete": True, "entries": [
            {"rank": 1, "repository": "acme/first", "repository_url": "https://github.com/acme/first", "stars_total": 1, "stars_today": 1},
            {"rank": 2, "repository": "acme/second", "repository_url": "https://github.com/acme/second", "stars_total": 2, "stars_today": 2},
        ]}), encoding="utf-8")
        return collect_snapshot(path, "2026-11-09", "2026-11-09T00:30:00Z")


if __name__ == "__main__":
    unittest.main()
