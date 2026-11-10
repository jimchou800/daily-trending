"""Hosted boundary tests: only network providers/authority are substituted."""

import copy
import io
import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from daily_trending.dashscope import BudgetExceeded, DashScopeClient, DashScopeConfig
from daily_trending.hosted import RemoteArtifactStore, RemoteAuthority, RemoteModelTransport
from daily_trending.hosted_daily import main, run_hosted_daily
from daily_trending.scheduler import BEIJING

PERIOD = "2026-11-10"
NOW = datetime(2026, 11, 10, 8, 15, tzinfo=BEIJING)


class AuthorityHTTP:
    def __init__(self):
        self.data = {
            "period": PERIOD,
            "attempts": 0,
            "fence": 0,
            "artifacts": {},
            "reservations": {},
            "month": {"reserved_micros": 0, "accounting_blocked": False},
            "history": [],
            "publication": None,
        }
        self.calls = []
        self.fail_stage = 0
        self.lose_publish_ack = False
        self.fail_publish = False
        self.runs = {}

    def __call__(self, method, path, body):
        self.calls.append((method, path, copy.deepcopy(body)))
        op = path.split("/")[-1].split("?")[0]
        if op == "state":
            return copy.deepcopy(self.data)
        if op == "begin":
            if body["run_id"] in self.runs:
                return self.runs[body["run_id"]]
            if self.data["attempts"] >= 3:
                raise RuntimeError("attempts exhausted")
            self.data["attempts"] += 1
            self.data["fence"] += 1
            token = {"fence": self.data["fence"], "attempts": self.data["attempts"]}
            self.runs[body["run_id"]] = token
            return token
        if op == "artifact":
            key, value = body["key"], body["value"]
            if key in self.data["artifacts"] and self.data["artifacts"][key] != value:
                raise RuntimeError("immutable artifact")
            self.data["artifacts"][key] = copy.deepcopy(value)
            return {"stored": True}
        if op == "reserve":
            self.data["reservations"][body["key"]] = copy.deepcopy(body)
            self.data["month"]["reserved_micros"] += body["amount_micros"]
            return {"dispatch": True, "reservation": body}
        if op == "usage":
            self.data["reservations"][body["key"]]["actual_micros"] = body["actual_micros"]
            self.data["month"]["accounting_blocked"] |= body["accounting_blocked"]
            return {"accounting_blocked": self.data["month"]["accounting_blocked"]}
        if op == "stage":
            if self.fail_stage:
                self.fail_stage -= 1
                raise ConnectionError("temporary stage failure")
            self.staged = copy.deepcopy(body["report"])
            return {"hash": "validated-hash"}
        if op == "publish":
            if self.fail_publish:
                raise ConnectionError("publication unavailable")
            self.staged.update(status="published", published_at="2026-11-10T00:20:00Z")
            self.data["publication"] = self.staged
            self.data["history"].append(self.staged)
            if self.lose_publish_ack:
                raise TimeoutError("lost acknowledgement")
            return {"report": self.staged}
        raise AssertionError(path)


def authority(http):
    return RemoteAuthority("https://example.test", "private-token", "installation", request=http)


def paid_response(payload, _key):
    prompt = payload["messages"][0]["content"]
    entries = json.loads(prompt.split("\n", 1)[1])
    if "classifications" in prompt.split("\n", 1)[0]:
        result = {
            "classifications": [
                {
                    "repository": e["repository"],
                    "is_ai": True,
                    "ai_relevance_evidence": "Build AI agents",
                    "ai_core_purpose": "tool",
                }
                for e in entries
            ]
        }
    else:
        entry = entries[0]
        result = dict(
            repository=entry["repository"],
            is_ai=True,
            ai_relevance_evidence="Build AI agents",
            project_type="工具",
            introduction="AI 工作流",
            problem_solved="自动处理",
            audience="开发者",
            characteristics="仓库描述明确",
            reason_has_evidence=False,
            reason=None,
            sources=[entry["repository_url"]],
        )
    return {
        "choices": [{"message": {"content": json.dumps(result)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


class HostedTests(unittest.TestCase):
    def test_remote_previous_prompt_cache_and_analyzer_stage_are_incompatible(self):
        http = AuthorityHTTP()
        for repo in ("source:sample/ui", "relevance:sample/ui", "sample/ui"):
            http.data["artifacts"][f"analysis:snapshot:{repo}:model:2:3"] = {"repository": repo, "is_ai": True}
        http.data["artifacts"]["analyzer"] = {
            "contract_version": "1.0",
            "period_id": PERIOD,
            "snapshot_id": "snapshot",
            "analysis_schema": "3",
            "stage": {"name": "analyzer", "status": "succeeded"},
        }
        store = RemoteArtifactStore(authority(http), PERIOD, 1)
        for repo in ("source:sample/ui", "relevance:sample/ui", "sample/ui"):
            self.assertIsNone(store.load_analysis(repo, "snapshot", "model"))
        self.assertIsNone(store.load_compatible("analyzer", "snapshot"))

    def test_model_timeout_settles_full_reservation_before_a_separately_charged_retry(self):
        http = AuthorityHTTP()
        dispatches = 0

        def model(payload, key):
            nonlocal dispatches
            dispatches += 1
            if dispatches == 1:
                raise TimeoutError("provider timeout")
            return {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}

        remote = authority(http)
        transport = RemoteModelTransport(remote, PERIOD, 1, model)
        payload = {"messages": [{"content": "x"}], "max_completion_tokens": 10}
        with self.assertRaises(TimeoutError):
            transport(payload, "secret")
        first = next(iter(http.data["reservations"].values()))
        self.assertEqual(first["actual_micros"], first["amount_micros"])
        usage = [body for _, path, body in http.calls if path.endswith("usage")][0]
        self.assertEqual(usage["usage_status"], "unavailable")
        self.assertFalse(usage["accounting_blocked"])
        transport(payload, "secret")
        self.assertEqual(dispatches, 2)
        self.assertEqual(len(http.data["reservations"]), 2)
        self.assertEqual(http.data["month"]["reserved_micros"], first["amount_micros"] * 2)

    def test_failed_timeout_settlement_blocks_next_dispatch(self):
        http = AuthorityHTTP()

        def request(method, path, body):
            if path.endswith("usage"):
                raise ConnectionError("settlement unavailable")
            return http(method, path, body)

        remote = authority(request)
        dispatches = []

        def model(*_):
            dispatches.append(True)
            raise TimeoutError("provider timeout")

        payload = {"messages": [{"content": "x"}], "max_completion_tokens": 10}
        for _ in range(2):
            with self.assertRaises(BudgetExceeded):
                RemoteModelTransport(remote, PERIOD, 1, model)(payload, "secret")
        self.assertEqual(len(dispatches), 1)

    def test_lost_reservation_ack_never_dispatches_model(self):
        calls = []

        def request(method, path, body):
            calls.append(path)
            if path.endswith("reserve"):
                raise TimeoutError("ack lost")
            return {"reservations": {"paid-key": {"amount_micros": 50}}}

        transport = RemoteModelTransport(authority(request), PERIOD, 1, lambda payload, key: self.fail("paid dispatch"))
        with self.assertRaises(BudgetExceeded):
            transport({"messages": [{"content": "hello"}], "max_completion_tokens": 10}, "key")
        self.assertEqual(calls[0], "/_automation/reserve")
        self.assertTrue(calls[1].startswith("/_automation/state?"))

    def test_bad_usage_persists_month_block_and_never_calls_again(self):
        http = AuthorityHTTP()
        transport = RemoteModelTransport(authority(http), PERIOD, 1, lambda *_: {"usage": {"prompt_tokens": -1}})
        payload = {"messages": [{"content": "hello"}], "max_completion_tokens": 10}
        for _ in range(2):
            with self.assertRaises(BudgetExceeded):
                transport(payload, "secret")
        self.assertTrue(http.data["month"]["accounting_blocked"])
        self.assertEqual(len(http.data["reservations"]), 1)

    def test_failed_usage_ack_blocks_all_subsequent_transports_in_process(self):
        http = AuthorityHTTP()

        def request(method, path, body):
            if path.endswith("/usage"):
                raise OSError("unavailable")
            return http(method, path, body)

        remote = authority(request)
        payload = {"messages": [{"content": "hello"}], "max_completion_tokens": 10}
        for _ in range(2):
            transport = RemoteModelTransport(
                remote, PERIOD, 1, lambda *_: {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            )
            with self.assertRaises(BudgetExceeded):
                transport(payload, "secret")
        self.assertEqual(len(http.data["reservations"]), 1)

    def test_usage_above_reserve_is_charged_and_blocks(self):
        http = AuthorityHTTP()
        transport = RemoteModelTransport(
            authority(http), PERIOD, 1, lambda *_: {"usage": {"prompt_tokens": 10000, "completion_tokens": 10}}
        )
        with self.assertRaises(BudgetExceeded):
            transport({"messages": [{"content": "x"}], "max_completion_tokens": 10}, "secret")
        record = next(iter(http.data["reservations"].values()))
        self.assertEqual(record["actual_micros"], 120360)
        self.assertTrue(http.data["month"]["accounting_blocked"])

    def test_duplicate_reservation_never_dispatches(self):
        transport = RemoteModelTransport(
            authority(lambda *_: {"dispatch": False}), PERIOD, 1, lambda *_: self.fail("duplicate paid call")
        )
        with self.assertRaises(BudgetExceeded):
            transport({"messages": [{"content": "x"}], "max_completion_tokens": 10}, "secret")

    def test_authority_rejects_untrusted_origins_and_bad_acknowledgements(self):
        for origin in (
            "http://example.test",
            "https://user:secret@example.test",
            "https://example.test/path",
            "https://example.test?token=x",
        ):
            with self.assertRaises(ValueError):
                RemoteAuthority(origin, "token", "installation")
        for value in ([], {"error": "unauthorized"}):
            with self.assertRaises(RuntimeError):
                authority(lambda *_: value).call("begin", {})

    def test_http_auth_stays_in_headers_and_response_is_json(self):
        remote = RemoteAuthority("https://example.test", "private-token", "installation")
        with patch("daily_trending.hosted.urlopen", return_value=io.BytesIO(b'{"ok": true}')) as opened:
            self.assertTrue(remote.call("begin", {"period": PERIOD})["ok"])
        req = opened.call_args.args[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer private-token")
        self.assertEqual(req.get_header("X-installation-id"), "installation")
        self.assertNotIn("private-token", req.full_url)

    def test_snapshot_survives_lost_collector_write_and_local_directory_loss(self):
        http = AuthorityHTTP()
        collector = {
            "contract_version": "1.0",
            "period_id": PERIOD,
            "snapshot_id": "id",
            "stage": {"name": "collector", "status": "succeeded"},
            "snapshot": {
                "source_url": "https://github.com/trending?since=daily",
                "collected_at": "2026-11-10T00:15:00Z",
                "entries": [],
            },
        }
        store = RemoteArtifactStore(authority(http), PERIOD, 1)
        store.save("collector", collector)
        del http.data["artifacts"]["collector"]
        restored = RemoteArtifactStore(authority(http), PERIOD, 2)
        self.assertEqual(restored.load_compatible("collector"), collector)
        self.assertIsNone(restored.load_compatible("collector", "wrong"))
        self.assertIsNone(restored.load_compatible("collector", model="wrong"))
        self.assertIsNone(restored.load_compatible("missing"))


class HostedRunnerTests(unittest.TestCase):
    def test_transient_initial_state_and_store_initialization_recover(self):
        for failed_state in (1, 2):
            with self.subTest(failed_state=failed_state):
                http = AuthorityHTTP()
                states = 0

                def request(method, path, body):
                    nonlocal states
                    if "state?" in path:
                        states += 1
                        if states == failed_state:
                            raise ConnectionError("temporary state outage")
                    return http(method, path, body)

                with TemporaryDirectory() as directory:
                    report = self.run_job(request, directory)
                self.assertEqual(report["status"], "published")
                self.assertEqual(http.data["attempts"], 1)

    def test_lost_begin_ack_reuses_identical_run_id_without_spending_an_attempt(self):
        http = AuthorityHTTP()
        lost = False

        def request(method, path, body):
            nonlocal lost
            result = http(method, path, body)
            if path.endswith("begin") and not lost:
                lost = True
                raise TimeoutError("begin acknowledgement lost")
            return result

        with TemporaryDirectory() as directory:
            report = self.run_job(request, directory)
        begins = [body["run_id"] for _, path, body in http.calls if path.endswith("begin")]
        self.assertEqual(len(begins), 2)
        self.assertEqual(begins[0], begins[1])
        self.assertEqual(http.data["attempts"], 1)
        self.assertEqual(report["status"], "published")

    def test_persistent_begin_transport_failure_is_bounded_without_new_run_ids(self):
        http = AuthorityHTTP()
        begins = []

        def request(method, path, body):
            if path.endswith("begin"):
                begins.append(body["run_id"])
                http(method, path, body)
                raise TimeoutError("lost every acknowledgement")
            return http(method, path, body)

        with TemporaryDirectory() as directory, self.assertRaises(TimeoutError):
            self.run_job(request, directory)
        self.assertEqual(len(begins), 3)
        self.assertEqual(len(set(begins)), 1)
        self.assertEqual(http.data["attempts"], 1)
        self.assertEqual(http.data["reservations"], {})

    def run_job(self, http, directory, **kwargs):
        client = DashScopeClient(
            api_key="private-model-key", transport=paid_response, config=DashScopeConfig(max_retries=0)
        )
        return run_hosted_daily(
            authority(http),
            Path(directory),
            client=client,
            clock=lambda: NOW,
            fetch=lambda _: Path("tests/fixtures/trending_page.html").read_text(encoding="utf-8"),
            source_loader=lambda _: {"readme": "Build AI agents"},
            **kwargs,
        )

    def test_real_pipeline_retries_publication_without_repaying_or_refetching_snapshot(self):
        http = AuthorityHTTP()
        http.fail_stage = 1
        with TemporaryDirectory() as directory:
            report = self.run_job(http, directory)
            self.assertTrue((Path(directory) / "index.html").exists())
        self.assertEqual(report["report_date"], PERIOD)
        self.assertEqual(report["status"], "published")
        self.assertEqual(report["entries"][0]["rank"], 1)
        self.assertEqual(http.data["attempts"], 2)
        self.assertEqual(len(http.data["history"]), 1)
        self.assertEqual(len(http.data["reservations"]), report["entry_count"] + 1)
        proof = http.data["artifacts"]["selection"]
        self.assertTrue(proof["completed"])
        self.assertEqual(proof["eligible_count"], report["entry_count"])
        for row in http.data["reservations"].values():
            self.assertEqual(row["actual_micros"], 840)
        with TemporaryDirectory() as empty_directory:
            same = self.run_job(http, empty_directory)
            self.assertEqual(same, report)
        self.assertEqual(http.data["attempts"], 2)

    def test_lost_publication_ack_recovers_canonical_success(self):
        http = AuthorityHTTP()
        http.lose_publish_ack = True
        with TemporaryDirectory() as directory:
            report = self.run_job(http, directory)
        self.assertEqual(report["published_at"], "2026-11-10T00:20:00Z")
        self.assertEqual(http.data["attempts"], 1)
        self.assertEqual(len(http.data["history"]), 1)

    def test_persisted_attempt_limit_survives_new_runner(self):
        http = AuthorityHTTP()
        http.fail_publish = True
        with TemporaryDirectory() as directory, self.assertRaises(RuntimeError):
            self.run_job(http, directory)
        self.assertEqual(http.data["attempts"], 3)
        paid_count = len(http.data["reservations"])
        with TemporaryDirectory() as directory, self.assertRaises(RuntimeError):
            self.run_job(http, directory)
        self.assertEqual(len(http.data["reservations"]), paid_count)
        self.assertIsNone(http.data["publication"])

    def test_late_and_naive_jobs_never_contact_authority(self):
        remote = authority(lambda *args: self.fail("late network"))
        for instant, error in (
            (datetime(2026, 11, 10, 9, tzinfo=BEIJING), TimeoutError),
            (datetime(2026, 11, 10, 8), ValueError),
        ):
            with TemporaryDirectory() as directory, self.assertRaises(error):
                run_hosted_daily(remote, Path(directory), clock=lambda: instant)

    def test_entrypoint_failure_does_not_print_secret_exception(self):
        with patch.dict("os.environ", {}, clear=True), patch("sys.stderr", new_callable=io.StringIO) as output:
            self.assertEqual(main(), 1)
        self.assertNotIn("KeyError", output.getvalue())

    def test_usage_is_persisted_after_response_crosses_deadline_without_publication(self):
        http = AuthorityHTTP()
        instant = [NOW]

        def transport(payload, key):
            response = paid_response(payload, key)
            instant[0] = datetime(2026, 11, 10, 9, tzinfo=BEIJING)
            return response

        client = DashScopeClient(api_key="test", transport=transport, config=DashScopeConfig(max_retries=0))
        with TemporaryDirectory() as directory, self.assertRaises(TimeoutError):
            run_hosted_daily(
                authority(http),
                Path(directory),
                client=client,
                clock=lambda: instant[0],
                fetch=lambda _: Path("tests/fixtures/trending_page.html").read_text(encoding="utf-8"),
                source_loader=lambda _: {"readme": "Build AI agents"},
            )
        self.assertEqual(len(http.data["reservations"]), 1)
        self.assertEqual(next(iter(http.data["reservations"].values()))["actual_micros"], 840)
        self.assertFalse(any(path.endswith("/publish") for _, path, _ in http.calls))
        self.assertIsNone(http.data["publication"])
