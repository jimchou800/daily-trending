from tests.model_stub import classification_response
import json
import os
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

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
    def test_github_api_request_uses_configured_workflow_token(self):
        from daily_trending.github_sources import _get_json

        response = type(
            "Response",
            (),
            {"read": lambda self: b'{"ok":true}', "__enter__": lambda self: self, "__exit__": lambda self, *_: None},
        )()
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}),
            patch("daily_trending.github_sources.urlopen", return_value=response) as urlopen_mock,
        ):
            self.assertEqual(_get_json("https://api.github.com/repos/acme/demo"), {"ok": True})
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")

    def test_github_source_loader_rejects_invalid_repository_and_handles_missing_sources(self):
        with self.assertRaisesRegex(ValueError, "invalid repository"):
            load_github_sources({"repository": "bad"}, lambda _: {})

        result = load_github_sources(
            {"repository": "acme/empty"},
            lambda url: [] if "/releases" in url else None,
        )
        self.assertEqual(result, {"metadata": None, "readme": "", "releases": []})

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
                    {
                        "tag_name": "v1",
                        "published_at": "2026-01-01",
                        "html_url": "https://github.com/acme/first/releases/v1",
                        "draft": False,
                        "prerelease": False,
                    },
                    {"tag_name": "v2", "draft": False, "prerelease": True},
                ]
            return {"full_name": "acme/first", "description": "AI"}

        result = load_github_sources({"repository": "acme/first"}, fetch)
        self.assertEqual(result["readme"], "# README")
        self.assertEqual([release["tag_name"] for release in result["releases"]], ["v1"])
        self.assertIs(result["releases"][0]["draft"], False)
        self.assertIs(result["releases"][0]["prerelease"], False)
        self.assertTrue(all(url.startswith("https://api.github.com/repos/acme/first") for url in urls))


class DashScopeTests(unittest.TestCase):
    def test_selected_model_disables_thinking_and_uses_bounded_configured_timeout(self):
        response = json.dumps(
            {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        ).encode()
        for model in ("dashscope/qwen3.8-max", "qwen3.8-max", "custom-model-v2"):
            client = DashScopeClient(api_key="test", config=DashScopeConfig(model=model))
            with patch("daily_trending.dashscope.urlopen", return_value=BytesIO(response)) as http:
                client.complete("source")
            payload = json.loads(http.call_args.args[0].data)
            if model.endswith("qwen3.8-max"):
                self.assertIs(payload["enable_thinking"], False)
            else:
                self.assertNotIn("enable_thinking", payload)
            self.assertEqual(http.call_args.kwargs["timeout"], 120)
        for timeout in (0, 181, True, 1.5):
            with self.assertRaises(ValueError):
                DashScopeConfig(request_timeout_seconds=timeout)

    def test_http_request_preserves_custom_remote_model_id(self):
        for model in ("qwen3.8-max", "custom-model-v2", "dashscope/custom-model-v2"):
            with self.subTest(model=model):
                client = DashScopeClient(api_key="test-key", config=DashScopeConfig(model=model))
                response = json.dumps(
                    {
                        "choices": [{"message": {"content": "{}"}}],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 2},
                    }
                ).encode()
                with patch("daily_trending.dashscope.urlopen", return_value=BytesIO(response)) as http:
                    self.assertEqual(client.complete("source text"), "{}")
                expected = "qwen3.8-max" if model == "qwen3.8-max" else "custom-model-v2"
                self.assertEqual(json.loads(http.call_args.args[0].data)["model"], expected)
                self.assertEqual(client.config.model, model)

    def test_invalid_model_identity_is_rejected_before_transport_or_spend(self):
        for model in (
            "",
            "dashscope/",
            "other/qwen3.8-max",
            "dashscope/dashscope/qwen3.8-max",
            " qwen3.8-max",
            "qwen 3",
        ):
            with self.subTest(model=model):
                client = DashScopeClient(api_key="test-key", config=DashScopeConfig(model=model))
                with patch("daily_trending.dashscope.urlopen") as http:
                    with self.assertRaisesRegex(ModelCallError, "model identifier"):
                        client.complete("source text")
                http.assert_not_called()
                self.assertEqual(client.calls_used, 0)
                self.assertEqual(client.estimated_spend_cny, 0)

    def test_http_request_uses_remote_model_id_without_changing_config_identity(self):
        config = DashScopeConfig()
        client = DashScopeClient(api_key="test-key", config=config)
        response = json.dumps(
            {
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
            }
        ).encode()
        with patch("daily_trending.dashscope.urlopen", return_value=BytesIO(response)) as http:
            self.assertEqual(json.loads(client.complete("source text")), {"ok": True})
        request = http.call_args.args[0]
        self.assertEqual(json.loads(request.data)["model"], "qwen3.8-max")
        self.assertEqual(json.loads(request.data)["max_completion_tokens"], 1800)
        self.assertNotIn("max_tokens", json.loads(request.data))
        self.assertEqual(config.model, "dashscope/qwen3.8-max")
        self.assertEqual(client.config.model, "dashscope/qwen3.8-max")

    def test_client_rejects_invalid_input_and_restored_usage(self):
        client = DashScopeClient(api_key="secret", transport=lambda *_: {})
        with self.assertRaisesRegex(ModelCallError, "empty"):
            client.complete("  ")
        with self.assertRaisesRegex(ModelCallError, "input exceeds"):
            DashScopeClient(
                api_key="secret",
                transport=lambda *_: {},
                config=DashScopeConfig(max_input_chars=2),
            ).complete("long")
        with self.assertRaisesRegex(ValueError, "persisted"):
            client.restore_usage(-1, 0)
        client.restore_usage(0, 0)
        self.assertEqual(client.calls_used, 0)

    def test_client_parses_text_blocks_and_rejects_non_object_or_long_output(self):
        def block_transport(payload, api_key):
            return {"choices": [{"message": {"content": [{"text": '```json\n{"ok":true}\n```'}]}}]}

        client = DashScopeClient(api_key="secret", transport=block_transport)
        self.assertEqual(json.loads(client.complete("source")), {"ok": True})

        array_client = DashScopeClient(
            api_key="secret",
            transport=lambda *_: {"choices": [{"message": {"content": "[]"}}]},
            config=DashScopeConfig(max_retries=0),
        )
        with self.assertRaisesRegex(ModelCallError, "object"):
            array_client.complete("source")

        long_client = DashScopeClient(
            api_key="secret",
            transport=lambda *_: {"choices": [{"message": {"content": '{"result":"long"}'}}]},
            config=DashScopeConfig(max_retries=2, max_output_chars=4),
        )
        with self.assertRaisesRegex(ModelCallError, "output exceeds"):
            long_client.complete("source")
        self.assertEqual(long_client.calls_used, 1)

    def test_client_enforces_per_run_call_limit(self):
        client = DashScopeClient(
            api_key="secret",
            transport=lambda *_: {"choices": [{"message": {"content": '{"ok":true}'}}]},
            config=DashScopeConfig(max_calls=1, estimated_call_cost_cny=0),
        )
        client.complete("first")
        with self.assertRaises(BudgetExceeded):
            client.complete("second")

    def test_analysis_prompt_contains_only_github_source_material(self):
        prompt = build_analysis_prompt(
            [
                {
                    "repository": "acme/first",
                    "repository_url": "https://github.com/acme/first",
                    "github_sources": {"readme": "# AI", "metadata": {"description": "AI"}},
                }
            ]
        )
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
        self.assertEqual(calls[0][0]["model"], "qwen3.8-max")

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
            if "classifications" in payload["messages"][0]["content"].split("\n", 1)[0]:
                return classification_response(payload, is_ai=True, evidence="AI tool")
            repository = json.loads(payload["messages"][0]["content"].split("\n", 1)[1])[0]["repository"]
            if repository == "acme/first":
                return {"choices": [{"message": {"content": "not json"}}]}
            return {"choices": [{"message": {"content": json.dumps({"is_ai": False})}}]}

        client = DashScopeClient(api_key="secret", transport=transport, config=DashScopeConfig(max_retries=0))
        result = analyze_live_snapshot(snapshot, client, lambda entry: {"readme": "AI tool"})
        self.assertEqual(result["stage"]["status"], "failed")
        self.assertEqual(result["analyses"], [])
        self.assertEqual(len(calls), 3)
        self.assertTrue(any("analysis failed" in error for error in result["stage"]["errors"]))

    def test_live_analyzer_accepts_a_verified_empty_ai_selection(self):
        snapshot = collect_snapshot_from_entries()

        def transport(payload, _api_key):
            return classification_response(payload, is_ai=False, evidence="ordinary utility")

        client = DashScopeClient(api_key="secret", transport=transport, config=DashScopeConfig(max_retries=0))
        result = analyze_live_snapshot(snapshot, client, lambda entry: {"readme": "ordinary utility"})
        self.assertEqual(result["stage"]["status"], "succeeded")
        self.assertEqual(result["analyses"], [])
        self.assertEqual(result["stage"]["errors"], [])

    def test_live_pipeline_writes_preview_from_saved_html_and_model_transport(self):
        from tempfile import TemporaryDirectory

        def transport(payload, api_key):
            if "classifications" in payload["messages"][0]["content"].split("\n", 1)[0]:
                data = classification_response(payload, is_ai=False, evidence="AI tool")
                value = json.loads(data["choices"][0]["message"]["content"])
                value["classifications"][0].update(is_ai=True, ai_core_purpose="tool")
                data["choices"][0]["message"]["content"] = json.dumps(value)
                return data
            repository = json.loads(payload["messages"][0]["content"].split("\n", 1)[1])[0]["repository"]
            response = {"repository": repository, "is_ai": repository == "acme/first"}
            if response["is_ai"]:
                response.update(
                    {
                        "ai_relevance_evidence": "README describes an AI tool.",
                        "project_type": "工具",
                        "introduction": "用于处理 AI 工作流。",
                        "problem_solved": "减少重复操作。",
                        "audience": "开发者",
                        "characteristics": "提供可复用流程。",
                        "reason_has_evidence": False,
                        "sources": ["https://github.com/acme/first"],
                    }
                )
            return {"choices": [{"message": {"content": json.dumps(response)}}]}

        client = DashScopeClient(api_key="secret", transport=transport, config=DashScopeConfig(max_retries=0))
        with TemporaryDirectory() as directory:
            result = run_live_pipeline(
                Path(directory),
                "2026-11-09",
                "2026-11-09T00:30:00Z",
                client,
                fetch=lambda url: TRENDING_HTML,
                source_loader=lambda entry: {"readme": "AI tool"},
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
        path.write_text(
            json.dumps(
                {
                    "complete": True,
                    "entries": [
                        {
                            "rank": 1,
                            "repository": "acme/first",
                            "repository_url": "https://github.com/acme/first",
                            "stars_total": 1,
                            "stars_today": 1,
                        },
                        {
                            "rank": 2,
                            "repository": "acme/second",
                            "repository_url": "https://github.com/acme/second",
                            "stars_total": 2,
                            "stars_today": 2,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return collect_snapshot(path, "2026-11-09", "2026-11-09T00:30:00Z")


if __name__ == "__main__":
    unittest.main()
