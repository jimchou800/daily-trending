"""Request-specific list-price reservation boundaries, without paid calls."""

import unittest
from unittest.mock import patch

from daily_trending.dashscope import BudgetExceeded, DashScopeClient, DashScopeConfig


def response(usage=None):
    result = {"choices": [{"message": {"content": "{}"}}]}
    if usage is not None:
        result["usage"] = usage
    return result


class TariffReservationTests(unittest.TestCase):
    def test_reserves_utf8_input_and_reasoning_inclusive_output_with_tolerance(self):
        client = DashScopeClient(api_key="test", transport=lambda *_: response())
        client.complete("中" * 1000)
        self.assertAlmostEqual(client.estimated_spend_cny, (3256 * 12 + 1810 * 36) / 1_000_000)

    def test_reservation_is_visible_to_existing_noarg_persistence_wrapper_before_network(self):
        recorded = []
        client = DashScopeClient(api_key="test", transport=lambda *_: recorded.append("network") or response())
        reserve = client._reserve_call

        def persist():
            reserve()
            recorded.append(client.estimated_spend_cny)

        client._reserve_call = persist
        client.complete("x" * 8000)
        self.assertAlmostEqual(recorded[0], (8256 * 12 + 1810 * 36) / 1_000_000)
        self.assertEqual(recorded[1], "network")

    def test_real_http_cannot_bypass_tariff_with_zero_override(self):
        client = DashScopeClient(
            api_key="test", config=DashScopeConfig(estimated_call_cost_cny=0, monthly_budget_cny=0.05)
        )
        with patch("daily_trending.dashscope.urlopen") as network:
            with self.assertRaises(BudgetExceeded):
                client.complete("source")
        network.assert_not_called()
        self.assertEqual(client.calls_used, 0)

    def test_retry_reserves_full_cost_again_without_refund(self):
        attempts = []

        def transport(*_):
            attempts.append(1)
            if len(attempts) == 1:
                raise TimeoutError("unknown billed result")
            return response({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

        client = DashScopeClient(api_key="test", transport=transport)
        client.complete("x")
        self.assertAlmostEqual(client.estimated_spend_cny, 2 * (257 * 12 + 1810 * 36) / 1_000_000)
        self.assertEqual(client.usage_records[0]["actual_cost_cny"], 0.000048)

    def test_excess_provider_usage_blocks_retries_and_future_calls(self):
        calls = []
        client = DashScopeClient(
            api_key="test",
            transport=lambda *_: calls.append(1) or response({"prompt_tokens": 10000, "completion_tokens": 2000}),
        )
        with self.assertRaisesRegex(BudgetExceeded, "reservation"):
            client.complete("x")
        with self.assertRaises(BudgetExceeded):
            client.complete("again")
        self.assertEqual(calls, [1])
        self.assertTrue(client.accounting_blocked)
        self.assertGreater(client.usage_records[0]["actual_cost_cny"], client.estimated_spend_cny)

    def test_malformed_usage_fails_closed_and_never_records_arbitrary_provider_fields(self):
        client = DashScopeClient(
            api_key="test", transport=lambda *_: response({"prompt_tokens": float("nan"), "secret": "sensitive"})
        )
        with self.assertRaises(BudgetExceeded):
            client.complete("x")
        self.assertTrue(client.accounting_blocked)
        self.assertNotIn("sensitive", str(client.usage_records))

    def test_all_numeric_configuration_is_checked_before_calls(self):
        invalid = {
            "max_input_chars": [0, -1, True, 1.5],
            "max_output_tokens": [0, 128001, True, float("inf")],
            "max_output_chars": [0, -1],
            "max_calls": [-1, True, 1.5],
            "max_retries": [-1, 3, True],
            "monthly_budget_cny": [-1, float("nan"), float("inf"), True],
            "estimated_call_cost_cny": [-1, float("nan"), float("inf"), True],
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    DashScopeClient(api_key="test", config=DashScopeConfig(**{field: value}))

    def test_total_mismatch_and_missing_http_usage_block_accounting(self):
        for usage in (None, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 3}):
            with self.subTest(usage=usage):
                client = DashScopeClient(api_key="test")
                import io
                import json

                with patch(
                    "daily_trending.dashscope.urlopen", return_value=io.BytesIO(json.dumps(response(usage)).encode())
                ):
                    with self.assertRaises(BudgetExceeded):
                        client.complete("x")
                self.assertTrue(client.accounting_blocked)
                self.assertEqual(client.calls_used, 1)

    def test_usage_at_local_limits_and_higher_floor_are_retained(self):
        client = DashScopeClient(
            api_key="test",
            config=DashScopeConfig(estimated_call_cost_cny=0.2),
            transport=lambda *_: response({"prompt_tokens": 257, "completion_tokens": 1810}),
        )
        self.assertEqual(client.complete("x"), "{}")
        self.assertEqual(client.estimated_spend_cny, 0.2)
        self.assertFalse(client.accounting_blocked)

    def test_implausible_provider_counts_fail_closed_without_numeric_overflow(self):
        client = DashScopeClient(
            api_key="test", transport=lambda *_: response({"prompt_tokens": 10**400, "completion_tokens": 1})
        )
        with self.assertRaises(BudgetExceeded):
            client.complete("x")
        self.assertTrue(client.accounting_blocked)

    def test_budget_cannot_exceed_product_cap_or_overflow(self):
        for value in (30.01, 10**400):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DashScopeConfig(monthly_budget_cny=value)
