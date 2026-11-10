# Request reservation and cost evidence

Verified public documentation: 2026-11-10. This is an engineering estimate, not account validation or seven-issue product acceptance. Subsequent bounded real requests and token-based cost calculations are recorded in [model validation](model-validation-2026-11-10.md).

Primary sources:

- Model/prices: https://help.aliyun.com/zh/model-studio/model-pricing
- Model limits: https://help.aliyun.com/zh/model-studio/text-generation-model
- Request parameters: https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions
- Regional endpoints: https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope

The Beijing `qwen3.8-max` standard list price is CNY 12 per million input tokens and CNY 36 per million output tokens, including reasoning and answer. No free allowance, cache discount, Batch discount, or currency conversion is assumed. The default Beijing endpoint uses these rates. Other models, regions, deployment modes and future price changes require fresh tariff verification before use; preserving a custom remote model identifier is not price validation.

`max_tokens` excludes reasoning for this model. The adapter sends `max_completion_tokens` (default 1800), which covers reasoning plus answer. The parameter documentation explicitly allows actual output to differ by up to 10 tokens, so the reservation includes 1810 output tokens by default.

For the selected `qwen3.8-max`, raw HTTP requests also send top-level `enable_thinking:false`, so the bounded completion allowance serves the JSON answer. Custom remote model IDs do not receive this model-specific field. The HTTP timeout defaults to 120 seconds, configurable from 1 to 180 seconds; the publication deadline is still checked before dispatch and before publication. A timeout does not refund the call or authorize late publication.

Before each attempt, including a retry:

```
local_input_estimate = UTF8_bytes(entire_user_message) + 256
output_estimate = max_completion_tokens + 10
calculated_CNY = (local_input_estimate * 12 + output_estimate * 36) / 1_000_000
reserved_CNY = max(calculated_CNY, configured_estimated_call_cost_floor)
```

The entire message includes instructions, source material, JSON/schema text and separators. The extra 256 input tokens explicitly allow for chat-template/service overhead. UTF-8 bytes plus 256 is a local conservative assumption, not a provider-guaranteed tokenizer bound: no exact tokenizer/chat-template accounting guarantee was found in the primary documentation. A single character can occupy multiple UTF-8 bytes. Do not equate character limits with input tokens. The adapter validates numeric limits and uses the calculated reservation even when the configured floor is zero. Reservation is made before transport via the existing no-argument reservation hook, allowing the pipeline to persist it before HTTP.

All reservations are retained even on timeout or malformed output; a low reported usage does not refund them. Real HTTP responses must report valid nonnegative integer prompt/completion usage. Only allowlisted numeric usage fields and status are recorded, never arbitrary response data, request text or credentials. Missing, invalid or inconsistent real usage, or reported token counts above either estimate, block further calls. An injected fixture transport may omit usage for existing offline samples; this exemption does not apply to real HTTP. Persistence of this accounting-stop state and usage records across process restarts is part of the pipeline integration contract.

In hosted operation a provider transport exception is settled at the full reserved bound with `usage_status:unavailable` before a separately reserved retry is allowed. This is a conservative charge, not measured provider usage. The ledger's legacy `actual_micros` field holds that full bound for unavailable outcomes and the measured cost for `reported` outcomes. Invalid or underestimated returned usage still blocks the month; a lost settlement acknowledgement blocks further dispatch. Unresolved reservations after runner loss remain pending and require accounting recovery; they are never refunded automatically.

## Normal workflow baseline

A bounded normal issue can use one full-ranking relevance request with at most 8000 UTF-8 bytes of complete prompt and five detail requests with at most 6000 UTF-8 bytes each; every request has an 1800-token total output cap. These are entire-message limits, not source-only limits. Under the local input assumption:

| Stage | Calls | Input estimate per call | Output per call incl. tolerance | CNY |
| --- | ---: | ---: | ---: | ---: |
| Full-ranking relevance | 1 | 8256 | 1810 | 0.164232 |
| Detail analysis | 5 | 6256 | 1810 | 0.701160 |
| Daily total | 6 | 39536 aggregate | 10860 aggregate | 0.865392 |
| 31-day model total | 186 | | | 26.827152 |

This leaves CNY 3.172848 for other monthly services or extra calls, assuming selected static hosting and scheduling have zero daily-service fees. It proves arithmetic feasibility only under those bounds. The analyzer must actually enforce these complete-prompt limits while preserving the complete ranking; truncating the ranking to meet the bound is not allowed. A larger ranking or unavailable reliable analysis must fail within budget, not silently omit candidates. The quality/adequacy of these prompt and output bounds needs sample and eventual live validation.

Failure retries are excluded from the normal baseline. Every extra attempt reserves its actual request-specific amount, successful work is reused, and both per-run call limits and the shared CNY 30 monthly cap apply. Two complete six-call attempts beyond the baseline would cost up to CNY 2.596176 for that issue; sustained failures can exhaust the month and must stop further paid calls. This is not a claim of 31 successful issues under repeated failures. Actual other service charges must reduce the model allowance; no free-tier assumption may replace that accounting. The 09:00 deadline and next-month recovery remain separate required checks.
