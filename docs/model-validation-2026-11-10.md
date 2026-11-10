# DashScope development validation — 2026-11-10

These are bounded development experiments, not published issues or evidence of
seven-day reader acceptance. No report was published after the 09:00 cutoff.
Credentials were read from the existing environment and were not saved in the
evidence or repository.

## Verified service and collection

- The authenticated, non-billable models endpoint returned `qwen3.8-max` among
  182 IDs. The application identity remains `dashscope/qwen3.8-max`; outbound
  requests use `qwen3.8-max`.
- The real Daily all-language page contained 11 repository cards. A Sponsor
  link preceding a repository heading exposed a parser defect, now covered by
  saved-page regression tests. This count describes that capture only.
- Anonymous repository API reads returned HTTP 403 rate-limit errors. The
  scheduled runner passes its built-in GitHub token; development source reads
  used the existing authenticated GitHub CLI token without logging it.

## Recorded requests

Costs below apply the published Beijing tariff of CNY12/million input tokens
and CNY36/million output tokens. They are calculations from returned token usage,
not a downloaded billing invoice. No free allowance or discount is assumed.

| Experiment | Result | Input/output tokens | Reserved CNY | Calculated CNY |
| --- | --- | --- | --- | --- |
| One-candidate connection smoke | Valid JSON | 129 / 387 | 0.071340 | 0.015480 |
| Eleven-candidate relevance, 30s transport | Timeout, outcome unknown | Unavailable | 0.105900 | Unknown; reservation retained |
| Same relevance, 120s transport | Empty answer after thinking consumed cap | 822 / 1800 | 0.105900 | 0.074664 |
| Non-thinking relevance | Answer returned in about 6s; schema validation failed | 773 / 310 | 0.105900 | 0.020436 |
| Fixed non-thinking prompt, complete saved-live preview | Five entries, six calls | 8610 / 1880 | 0.810708 | 0.171000 |
| Core-purpose classification | Design system correctly excluded; another synthesized quote rejected | 894 / 494 | 0.114492 | 0.028512 |
| Core-purpose and quote-recovery preview | Five valid entries, six calls | 8290 / 1900 | 0.811200 | 0.167880 |

Known calculated consumption is CNY0.477972. Including the full unknown request
reservation gives CNY0.583872 conservatively; reservations for known requests
also remain unreleased in their test ledgers. These experiments must not be
reported as a successful complete live pipeline or monthly capacity acceptance.
The first non-thinking run exposed missing required evidence fields and prompted a
stricter relevance-output schema instruction. A single connection smoke also
does not establish factual correctness of generated prose.

## Resulting constraints

The official API supports `enable_thinking` for Qwen3.8. Raw HTTP sends it at
the top level, unlike SDK `extra_body`. Non-thinking mode preserves the bounded
answer allowance; raising the output cap to accommodate reasoning would change
the cost envelope. Transport timeout must also be bounded and configured rather
than relying on the inadequate 30-second default. Mandatory response validation
continues to reject malformed or incomplete classifications.

Sources, checked on the validation date:

- <https://help.aliyun.com/zh/model-studio/model-pricing>
- <https://help.aliyun.com/zh/model-studio/models>
- <https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions>
- <https://github.com/trending?since=daily>

The complete preview used the saved real eleven-entry page and previously
fetched GitHub source responses, with six new real model calls. Its reserved
daily envelope projects to CNY25.131948 for 31 identical days; calculated usage
projects to CNY5.301000. These figures describe this sample, not a guarantee for
longer lists or retries. Review identified a possible AI-core false positive
in the generated selection, so that first execution was not factual acceptance.
The subsequent core-purpose preview excluded the generic design system and
selected Skills, Claude Quickstarts, GBrain, agent-browser and RuView in original
order. It used CNY0.811200 of conservative reservations, or CNY25.147200 over
31 identical days; calculated token usage was CNY0.167880, or CNY5.204280 over
31 identical days. Malformed batches still fail; individual unreliable source
quotes are excluded conservatively, and wholly unreliable classification fails.
This is a saved-live-source preview, not an on-time public issue or seven-day
reader acceptance.

See [cost model](cost-model.md) for the separate conservative normal-day capacity
calculation. These experiments did not establish selection/fact review or deployed
quota/timing validation. The initiator subsequently accepted the current first
release and removed the seven-issue reading gate on 2026-11-10; see the
[acceptance record](acceptance-trial.md). The experiment results above remain
historical evidence with their stated limits.
