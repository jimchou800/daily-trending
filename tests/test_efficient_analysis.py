import json
import tempfile
import unittest
from pathlib import Path
from daily_trending.analyzer import analyze_live_snapshot
from daily_trending.analysis_prompts import relevance_context, detail_prompt
from daily_trending.collector import collect_snapshot
from daily_trending.dashscope import DashScopeClient, DashScopeConfig
from daily_trending.persistence import ArtifactStore


class EfficientAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.calls = []
        self.details_failed = set()
        self.verdict = True
        self.classification_transform = lambda values: values

    def snapshot(self, count=25):
        entries = [
            {
                "repository": f"acme/p{i}",
                "repository_url": f"https://github.com/acme/p{i}",
                "rank": i + 1,
                "stars_today": 1,
                "stars_total": 10,
            }
            for i in range(count)
        ]
        path = self.root / "snapshot.json"
        print(path)
        path.write_text(json.dumps({"complete": True, "entries": entries}), encoding="utf-8")
        return collect_snapshot(path, "2026-11-09", "2026-11-09T00:30:00Z")

    def transport(self, payload, _):
        prompt = payload["messages"][0]["content"]
        data = json.loads(prompt.split("\n", 1)[1])
        self.calls.append((prompt, data))
        if "classifications" in prompt.split("\n", 1)[0]:
            result = {
                "classifications": self.classification_transform(
                    [
                        {
                            "repository": e["repository"],
                            "is_ai": self.verdict,
                            "ai_relevance_evidence": "Build AI agents" if self.verdict is not None else "",
                            "ai_core_purpose": "tool" if self.verdict else None,
                        }
                        for e in data
                    ]
                )
            }
        else:
            e = data[0]
            if e["repository"] in self.details_failed:
                return {"choices": [{"message": {"content": "{}"}}]}
            result = {
                "repository": e["repository"],
                "is_ai": True,
                "ai_relevance_evidence": "Build AI agents",
                "project_type": "tool",
                "introduction": "intro",
                "problem_solved": "problem",
                "audience": "developers",
                "characteristics": "simple",
                "reason_has_evidence": False,
                "sources": [e["repository_url"]],
            }
        return {"choices": [{"message": {"content": json.dumps(result)}}]}

    def run_analysis(self, snapshot, **kwargs):
        client = DashScopeClient(api_key="test", transport=self.transport, config=DashScopeConfig(max_retries=0))
        return analyze_live_snapshot(
            snapshot, client, kwargs.pop("loader", lambda _: {"readme": "Build AI agents"}), **kwargs
        )

    def test_agent_ready_design_system_is_excluded_despite_model_positive(self):
        self.classification_transform = lambda values: [
            dict(v, ai_core_purpose="tool", ai_relevance_evidence="agent ready") for v in values
        ]
        result = self.run_analysis(
            self.snapshot(1),
            loader=lambda _: {
                "metadata": {"description": "A design system for building interfaces, agent ready."},
                "readme": '<!-- build metadata -->\n<div align="center"><img src="badge.svg"></div>\n'
                "A design system for building interfaces, agent ready.",
            },
        )
        self.assertEqual(result["stage"]["status"], "succeeded")
        self.assertEqual(result["analyses"], [])
        self.assertEqual(result["selection"][0]["reason"], "unknown_relevance")
        self.assertEqual(len(self.calls), 1)

    def test_readme_prose_survives_leading_comments_badges_and_html(self):
        entry = self.snapshot(1)["snapshot"]["entries"][0]
        quote = "Skills teach Claude how to complete specific tasks."
        source = {
            "readme": "<!--" + ("generated header " * 100) + "-->\n"
            '<div align="center"><img alt="agent ready" src="badge.svg"></div>\n'
            "[![build](badge.svg)](https://example.test)\n# Skills\n" + quote
        }
        context = relevance_context(entry, source)
        print(context)
        self.assertIn(quote, context["readme"])
        self.assertNotIn("generated header", context["readme"])
        self.assertNotIn("agent ready", context["readme"])
        self.assertIn(quote, json.loads(detail_prompt(entry, source).split("\n", 1)[1])[0]["readme"])

    def test_core_ai_tools_learning_and_collections_remain_eligible(self):
        examples = [
            ("tool", "Skills teach Claude how to complete specific tasks."),
            ("tutorial", "通过本教程学习训练大模型。"),
            ("course", "机器学习课程，讲解模型训练与推理。"),
            ("resource_collection", "人工智能学习资料合集，收录课程与模型工具。"),
            ("infrastructure", "Serve language models with GPU inference."),
        ]
        for purpose, quote in examples:
            with self.subTest(purpose=purpose):
                self.calls.clear()
                self.classification_transform = lambda values: [
                    dict(v, ai_core_purpose=purpose, ai_relevance_evidence=quote) for v in values
                ]
                result = self.run_analysis(self.snapshot(1), loader=lambda _: {"readme": quote})
                self.assertEqual([a["rank"] for a in result["analyses"]], [1])
                self.assertEqual(result["analyses"][0]["ai_relevance_evidence"], quote)
                self.assertEqual(len(self.calls), 2)

    def test_compatibility_and_ai_assisted_development_are_not_core_purpose(self):
        for quote in ("AI-compatible components", "Built using Claude", "使用AI开发", "AI友好"):
            with self.subTest(quote=quote):
                self.calls.clear()
                self.classification_transform = lambda values: [
                    dict(v, ai_core_purpose="tool", ai_relevance_evidence=quote) for v in values
                ]
                result = self.run_analysis(self.snapshot(1), loader=lambda _: {"readme": quote})
                self.assertEqual(result["stage"]["status"], "succeeded")
                self.assertEqual(result["analyses"], [])
                self.assertEqual(result["selection"][0]["reason"], "unknown_relevance")
                self.assertEqual(len(self.calls), 1)

    def test_positive_without_core_purpose_is_conservatively_unknown(self):
        self.classification_transform = lambda values: [dict(v, ai_core_purpose=None) for v in values]
        result = self.run_analysis(self.snapshot(1))
        self.assertEqual(result["selection"][0]["reason"], "unknown_relevance")
        self.assertEqual(result["analyses"], [])
        self.assertEqual(len(self.calls), 1)

    def test_summarized_quote_excludes_one_candidate_without_discarding_valid_peers(self):
        self.classification_transform = lambda values: [
            dict(v, ai_relevance_evidence="Build...AI agents") if index == 1 else v for index, v in enumerate(values)
        ]
        result = self.run_analysis(self.snapshot(3))
        self.assertEqual(result["stage"]["status"], "succeeded")
        self.assertEqual([a["rank"] for a in result["analyses"]], [1, 3])
        rejected = result["classifications"][1]
        self.assertIsNone(rejected["is_ai"])
        self.assertEqual(rejected["ai_relevance_evidence"], "")
        self.assertIn("source quote", rejected["relevance_error"])
        decision = result["selection_proof"]["decisions"][1]
        self.assertEqual(decision["reason"], "unknown_relevance")
        self.assertEqual(decision["outcome"], "excluded")
        self.assertEqual(len(self.calls), 3)

    def test_unreliable_candidate_exclusion_and_valid_peers_survive_cache_restart(self):
        snapshot = self.snapshot(3)
        store = ArtifactStore(self.root / "quote-cache", snapshot["period_id"])
        self.classification_transform = lambda values: [
            dict(v, ai_relevance_evidence="Build...AI agents") if index == 1 else v for index, v in enumerate(values)
        ]
        self.run_analysis(snapshot, cache_store=store)
        self.calls.clear()
        result = self.run_analysis(snapshot, cache_store=store, loader=lambda _: self.fail("must reuse sources"))
        self.assertEqual(result["stage"]["status"], "succeeded")
        self.assertEqual([a["rank"] for a in result["analyses"]], [1, 3])
        self.assertIn("source quote", result["classifications"][1]["relevance_error"])
        self.assertTrue(all("relevance_error" not in a for a in result["analyses"]))
        self.assertEqual(self.calls, [])

    def test_valid_negative_and_unknown_remain_complete_despite_one_unreliable_verdict(self):
        self.classification_transform = lambda values: [
            dict(values[0], ai_relevance_evidence="Build...AI agents"),
            dict(values[1], is_ai=False, ai_core_purpose=None),
            dict(values[2], is_ai=None, ai_core_purpose=None, ai_relevance_evidence=""),
        ]
        result = self.run_analysis(self.snapshot(3))
        self.assertEqual(result["stage"]["status"], "succeeded")
        self.assertTrue(result["selection_proof"]["completed"])
        self.assertEqual(result["analyses"], [])
        self.assertEqual(len(self.calls), 1)

    def test_all_unreliable_quotes_are_failure_and_diagnostics_cannot_come_from_model(self):
        self.classification_transform = lambda values: [
            dict(v, ai_relevance_evidence="Build...AI agents", relevance_error="model supplied diagnostic")
            for v in values
        ]
        result = self.run_analysis(self.snapshot(2))
        self.assertEqual(result["stage"]["status"], "failed")
        self.assertFalse(result["selection_proof"]["completed"])
        self.assertEqual(result["analyses"], [])
        self.assertTrue(all("source quote" in v["relevance_error"] for v in result["classifications"]))
        self.assertIn("all relevance evidence is unreliable", result["stage"]["errors"][0])
        self.assertEqual(len(self.calls), 1)

        self.classification_transform = lambda values: [
            dict(v, relevance_error="model supplied diagnostic") for v in values
        ]
        result = self.run_analysis(self.snapshot(1))
        self.assertEqual(result["stage"]["status"], "succeeded")
        self.assertNotIn("relevance_error", result["classifications"][0])

    def test_all_candidates_classified_then_repeat_filter_before_five_details(self):
        snapshot = self.snapshot()
        history = [
            {"status": "published", "report_date": "2026-11-08", "entries": [snapshot["snapshot"]["entries"][0]]}
        ]
        result = self.run_analysis(snapshot, history=history)
        self.assertEqual(
            [e["repository"] for e in result["analyses"]], ["acme/p1", "acme/p2", "acme/p3", "acme/p4", "acme/p5"]
        )
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(len(self.calls[0][1]), 25)
        self.assertEqual(result["selection_proof"]["eligible_count"], 5)
        self.assertEqual(len(result["selection_proof"]["decisions"]), 25)
        self.assertEqual(result["selection_proof"]["decisions"][0]["outcome"], "excluded")
        self.assertEqual(result["selection_proof"]["decisions"][1]["outcome"], "selected")

    def test_failed_detail_continues_to_sixth_eligible_without_reordering(self):
        self.details_failed = {"acme/p0"}
        result = self.run_analysis(self.snapshot(8))
        self.assertEqual([e["rank"] for e in result["analyses"]], [2, 3, 4, 5, 6])
        self.assertEqual(len(self.calls), 7)
        self.assertEqual(result["stage"]["status"], "succeeded")
        self.assertTrue(any("acme/p0" in error for error in result["stage"]["errors"]))

    def test_unknown_is_excluded_but_incomplete_classification_is_failure(self):
        self.verdict = None
        snapshot = self.snapshot(2)
        result = self.run_analysis(snapshot)
        self.assertEqual(result["stage"]["status"], "succeeded")
        self.assertEqual([d["reason"] for d in result["selection"]], ["unknown_relevance", "unknown_relevance"])
        self.assertIsNone(result["selection_proof"]["decisions"][0]["is_ai"])
        self.classification_transform = lambda values: values[:1]
        result = self.run_analysis(snapshot)
        self.assertEqual(result["stage"]["status"], "failed")
        self.assertEqual(result["analyses"], [])
        self.assertEqual(len(self.calls), 2)

    def test_all_detail_failures_are_not_true_zero(self):
        self.details_failed = {"acme/p0", "acme/p1"}
        result = self.run_analysis(self.snapshot(2))
        self.assertEqual(result["stage"]["status"], "failed")
        self.assertEqual(result["analyses"], [])
        self.assertEqual(len(self.calls), 3)

    def test_later_repositories_beyond_any_assumed_list_size_are_classified(self):
        self.verdict = None
        result = self.run_analysis(self.snapshot(63))
        self.assertEqual(result["stage"]["status"], "succeeded")
        self.assertEqual(len(result["classifications"]), 63)
        self.assertEqual([len(data) for _, data in self.calls], [25, 25, 13])
        self.assertEqual(result["classifications"][-1]["repository"], "acme/p62")

    def test_relevance_requires_actual_source_quote_not_repository_name(self):
        self.classification_transform = lambda values: [dict(v, ai_relevance_evidence=v["repository"]) for v in values]
        result = self.run_analysis(self.snapshot(2))
        self.assertEqual(result["stage"]["status"], "failed")
        self.assertEqual(result["analyses"], [])
        self.assertEqual(len(self.calls), 1)

    def test_source_and_successes_are_reused_after_restart_without_refetch(self):
        snapshot = self.snapshot(7)
        store = ArtifactStore(self.root / "cache", snapshot["period_id"])
        self.details_failed = {"acme/p0"}
        result = self.run_analysis(snapshot, cache_store=store)
        self.assertEqual([e["rank"] for e in result["analyses"]], [2, 3, 4, 5, 6])
        self.details_failed = set()
        self.calls.clear()
        result = self.run_analysis(snapshot, cache_store=store, loader=lambda _: self.fail("sources must be reused"))
        self.assertEqual([e["rank"] for e in result["analyses"]], [1, 2, 3, 4, 5])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1][0]["repository"], "acme/p0")

    def test_large_multibyte_sources_fit_both_input_byte_budgets(self):
        result = self.run_analysis(
            self.snapshot(), loader=lambda _: {"readme": "Build AI agents" + ('中文\\"' * 20000)}
        )
        self.assertEqual(len(result["analyses"]), 5)
        self.assertLessEqual(len(self.calls[0][0].encode("utf-8")), 8000)
        self.assertTrue(all(len(prompt.encode("utf-8")) <= 6000 for prompt, _ in self.calls[1:]))
        self.assertTrue(all(len(data[0]["readme"]) < 20000 for _, data in self.calls[1:]))

    def test_relevance_prompt_specifies_complete_positive_negative_and_unknown_objects(self):
        self.run_analysis(self.snapshot(3))
        header = self.calls[0][0].split("\n", 1)[0]
        examples = json.loads(header.split("JSON示例：", 1)[1])["classifications"]
        self.assertEqual([e["is_ai"] for e in examples], [True, False, None])
        self.assertTrue(
            all(set(e) == {"repository", "is_ai", "ai_relevance_evidence", "ai_core_purpose"} for e in examples)
        )
        self.assertEqual(examples[-1]["ai_relevance_evidence"], "")

    def test_source_failure_is_incomplete_even_when_other_repository_is_non_ai(self):
        self.verdict = None

        def loader(entry):
            if entry["rank"] == 1:
                raise OSError("GitHub unavailable")
            return {"readme": "Build AI agents"}

        result = self.run_analysis(self.snapshot(2), loader=loader)
        self.assertEqual(result["stage"]["status"], "failed")
        self.assertEqual(result["analyses"], [])

    def test_all_recent_duplicates_need_no_detailed_model_calls(self):
        snapshot = self.snapshot(2)
        history = [{"status": "published", "report_date": "2026-11-08", "entries": snapshot["snapshot"]["entries"]}]
        result = self.run_analysis(snapshot, history=history)
        self.assertEqual(result["stage"]["status"], "succeeded")
        self.assertEqual(result["analyses"], [])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([d["reason"] for d in result["selection"]], ["recent_duplicate", "recent_duplicate"])
