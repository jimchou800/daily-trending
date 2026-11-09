"""Budget-limited adapter for DashScope's OpenAI-compatible endpoint."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.request import Request, urlopen


class ModelCallError(RuntimeError):
    """Raised when a model call cannot produce a valid JSON response."""


class BudgetExceeded(ModelCallError):
    """Raised before an API request when configured budget or call limits are exhausted."""


@dataclass(frozen=True)
class DashScopeConfig:
    """Conservative per-run and monthly limits for model requests."""

    model: str = "dashscope/qwen3.8-max"
    endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    max_input_chars: int = 12000
    max_output_tokens: int = 1800
    max_output_chars: int = 16000
    max_calls: int = 40
    max_retries: int = 2
    monthly_budget_cny: float = 30.0
    estimated_call_cost_cny: float = 0.05


class DashScopeClient:
    """Call DashScope with explicit input, output, retry, and budget limits."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        config: DashScopeConfig | None = None,
        transport: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("AI_DASHSCOPE_API_KEY")
        self.config = config or DashScopeConfig()
        self.transport = transport or self._http_transport
        self.calls_used = 0
        self.estimated_spend_cny = 0.0

    def restore_usage(self, calls_used: int, estimated_spend_cny: float) -> None:
        """Restore durable usage before a resumed run can make another request."""
        if calls_used < 0 or estimated_spend_cny < 0:
            raise ValueError("invalid persisted model usage")
        self.calls_used = calls_used
        self.estimated_spend_cny = estimated_spend_cny

    def complete(self, source_text: str) -> str:
        """Return a validated JSON model response, retrying transient failures."""
        if not self.api_key:
            raise ModelCallError("AI_DASHSCOPE_API_KEY is not configured")
        if not isinstance(source_text, str) or not source_text.strip():
            raise ModelCallError("model input is empty")
        if len(source_text) > self.config.max_input_chars:
            raise ModelCallError("model input exceeds configured limit")
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": source_text}],
            "max_tokens": self.config.max_output_tokens,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        last_error: Exception | None = None
        for _attempt in range(self.config.max_retries + 1):
            self._reserve_call()
            try:
                response = self.transport(payload, self.api_key)
                content = self._extract_content(response)
                if len(content) > self.config.max_output_chars:
                    raise ModelCallError("model output exceeds configured limit")
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ModelCallError("model response must be a JSON object")
                return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            except BudgetExceeded:
                raise
            except (ModelCallError, ValueError, TypeError, ConnectionError, TimeoutError, OSError) as exc:
                last_error = exc
                if isinstance(exc, ModelCallError) and "output" in str(exc):
                    break
        raise ModelCallError(f"model call failed: {last_error}") from last_error

    def _reserve_call(self) -> None:
        if self.calls_used >= self.config.max_calls:
            raise BudgetExceeded("model call limit exhausted")
        next_spend = self.estimated_spend_cny + self.config.estimated_call_cost_cny
        if next_spend > self.config.monthly_budget_cny:
            raise BudgetExceeded("monthly model budget exhausted")
        self.calls_used += 1
        self.estimated_spend_cny = next_spend

    @staticmethod
    def _extract_content(response: dict[str, Any]) -> str:
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelCallError("model response has no message content") from exc
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        if not isinstance(content, str) or not content.strip():
            raise ModelCallError("model response content is empty")
        cleaned = content.strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return cleaned

    def _http_transport(self, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.config.endpoint,
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))


def build_analysis_prompt(entries: list[dict]) -> str:
    """Build a source-bounded prompt from GitHub snapshot metadata."""
    source = []
    for entry in entries:
        github_sources = entry.get("github_sources", {})
        metadata = github_sources.get("metadata", {}) if isinstance(github_sources, dict) else {}
        safe_metadata = {
            key: metadata.get(key)
            for key in (
                "name",
                "full_name",
                "description",
                "topics",
                "language",
                "license",
                "stargazers_count",
                "forks_count",
            )
            if key in metadata
        }
        safe_releases = []
        for release in github_sources.get("releases", []) if isinstance(github_sources, dict) else []:
            if isinstance(release, dict):
                safe_releases.append({key: release.get(key) for key in ("tag_name", "published_at", "html_url")})
        source.append(
            {
                "repository": entry.get("repository"),
                "repository_url": entry.get("repository_url"),
                "description": entry.get("description", ""),
                "language": entry.get("language"),
                "rank": entry.get("rank"),
                "github_sources": {
                    "metadata": safe_metadata,
                    "readme": github_sources.get("readme", "") if isinstance(github_sources, dict) else "",
                    "releases": safe_releases,
                },
            }
        )
    return (
        "仅依据以下 GitHub Trending 条目及其 GitHub 仓库公开资料判断 AI 相关性。"
        "不要访问或引用 GitHub 之外的网页，不要编造热度数值。"
        "输出 JSON 数组 analyses，每项包含 repository、is_ai、ai_relevance_evidence、"
        "project_type、introduction、problem_solved、audience、characteristics、"
        "reason_has_evidence、reason、sources；sources 只能是 GitHub URL。\n" + json.dumps(source, ensure_ascii=False)
    )
