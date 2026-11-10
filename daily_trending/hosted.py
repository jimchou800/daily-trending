"""Private HTTP adapters for the durable hosted publication authority."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from .contracts import CONTRACT_VERSION, require_date
from .dashscope import (
    BudgetExceeded,
    CHAT_INPUT_OVERHEAD,
    INPUT_CNY_PER_MILLION,
    OUTPUT_CNY_PER_MILLION,
    OUTPUT_TOKEN_TOLERANCE,
)

MICROS_PER_CNY = 1_000_000
MAX_AUTHORITY_RESPONSE_BYTES = 4 * 1024 * 1024
CACHE_PROMPT_VERSION = "1"
CACHE_SCHEMA_VERSION = "1"


class RemoteAuthority:
    """Use one pinned HTTPS installation; transport failures never imply success."""

    def __init__(self, base_url: str, token: str, installation: str, *, request: Callable | None = None) -> None:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("PUBLICATION_BASE_URL must be an HTTPS origin")
        if parsed.path not in ("", "/") or not token or not installation:
            raise ValueError("publication origin and installation credentials are required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.installation = installation
        self._request = request or self._http_request

    def call(self, operation: str, body: dict, *, method: str = "POST") -> dict:
        """Send a private operation and reject malformed/nonobject acknowledgements."""
        value = self._request(method, f"/_automation/{operation}", body)
        if not isinstance(value, dict) or "error" in value:
            raise RuntimeError("publication authority rejected operation")
        return value

    def state(self, period: str) -> dict:
        """Read authoritative recovery state, including after the publication deadline."""
        require_date(period, "period")
        return self.call(f"state?{urlencode({'period': period})}", {}, method="GET")

    def _http_request(self, method: str, path: str, body: dict) -> dict:
        data = None if method == "GET" else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Installation-ID": self.installation,
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=30) as response:
            content = response.read(MAX_AUTHORITY_RESPONSE_BYTES + 1)
        if len(content) > MAX_AUTHORITY_RESPONSE_BYTES:
            raise ValueError("authority response exceeds size limit")
        return json.loads(content.decode("utf-8"))


class RemoteModelTransport:
    """Reserve every paid transport attempt remotely and persist usage before returning."""

    def __init__(self, authority: RemoteAuthority, period: str, fence: int, transport: Callable) -> None:
        self.authority = authority
        self.period = period
        self.fence = fence
        self.transport = transport
        self.blocked = False

    def __call__(self, payload: dict, api_key: str) -> dict:
        """Dispatch only on a positive durable first-reservation acknowledgement.

        A lost acknowledgement is queried for recovery, but cannot grant dispatch.
        Failed usage writes propagate before any further model request is possible.
        """
        if self.blocked or getattr(self.authority, "accounting_uncertain", False):
            raise BudgetExceeded("remote model accounting is unresolved")
        input_tokens = sum(len(item["content"].encode("utf-8")) for item in payload["messages"]) + CHAT_INPUT_OVERHEAD
        output_tokens = payload["max_completion_tokens"] + OUTPUT_TOKEN_TOLERANCE
        amount = input_tokens * INPUT_CNY_PER_MILLION + output_tokens * OUTPUT_CNY_PER_MILLION
        key = uuid4().hex
        reservation = {
            "period": self.period,
            "fence": self.fence,
            "key": key,
            "amount_micros": amount,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        try:
            ack = self.authority.call("reserve", reservation)
        except Exception as exc:
            try:
                self.authority.state(self.period)
            except Exception:
                pass
            raise BudgetExceeded("remote reservation acknowledgement unavailable; dispatch forbidden") from exc
        if ack.get("dispatch") is not True:
            raise BudgetExceeded("remote reservation did not grant first dispatch")
        try:
            response = self.transport(payload, api_key)
        except Exception:
            self.authority.accounting_uncertain = True
            raise
        usage = response.get("usage") if isinstance(response, dict) else None
        valid = isinstance(usage, dict) and all(
            type(usage.get(name)) is int and usage[name] >= 0 for name in ("prompt_tokens", "completion_tokens")
        )
        if valid:
            prompt, output = usage["prompt_tokens"], usage["completion_tokens"]
            valid = (
                prompt <= 1_000_000
                and output <= 128_010
                and type(usage.get("total_tokens", prompt + output)) is int
                and usage.get("total_tokens", prompt + output) == prompt + output
            )
        actual = prompt * INPUT_CNY_PER_MILLION + output * OUTPUT_CNY_PER_MILLION if valid else amount
        blocked = not valid or prompt > input_tokens or output > output_tokens or actual > amount
        try:
            ack = self.authority.call(
                "usage", {"period": self.period, "key": key, "actual_micros": actual, "accounting_blocked": blocked}
            )
        except Exception as exc:
            self.authority.accounting_uncertain = True
            self.blocked = True
            raise BudgetExceeded("remote usage acknowledgement unavailable; further dispatch forbidden") from exc
        if blocked or ack.get("accounting_blocked") is True:
            self.blocked = True
            raise BudgetExceeded("remote model accounting blocked")
        return response


class RemoteArtifactStore:
    """Expose pipeline caching over immutable authority artifacts, never local files."""

    def __init__(self, authority: RemoteAuthority, period: str, fence: int) -> None:
        self.authority, self.period, self.fence = authority, period, fence
        self.remote = authority.state(period)
        self.artifacts = self.remote["artifacts"]
        self.state = {"calls_used": len(self.remote["reservations"]), "estimated_spend_cny": 0.0}

    def monthly_usage(self) -> dict:
        """Restore the authoritative month total and persistent accounting latch."""
        month = self.remote["month"]
        return {
            "estimated_spend_cny": month["reserved_micros"] / MICROS_PER_CNY,
            "calls_used": len(self.remote["reservations"]),
            "accounting_blocked": month["accounting_blocked"],
            "usage_records": [],
        }

    def begin_run(self) -> None:
        """Keep the already-reserved remote attempt; never increment it locally."""

    def finish_run(self, status: str, failure_reason: str | None = None) -> None:
        """Record internal attempt outcome privately under its unique fencing token."""
        self._put(f"run:{self.fence}:{status}", {"status": status, "failure_reason": failure_reason})

    def update_usage(self, calls_used: int, estimated_spend_cny: float, **kwargs: Any) -> None:
        """Keep pipeline counters disposable; paid admission uses remote reservations."""
        self.state.update(calls_used=calls_used, estimated_spend_cny=estimated_spend_cny)

    def update_accounting(self, *args: Any, **kwargs: Any) -> None:
        """Leave accounting to the transport's durable per-call usage operation."""

    def _put(self, key: str, value: dict) -> None:
        self.authority.call(
            "artifact", {"period": self.period, "fence": self.fence, "key": key, "value": value}, method="PUT"
        )
        self.artifacts[key] = value

    def save(self, stage: str, artifact: dict) -> None:
        """Store a stage privately, preserving first snapshot before downstream work."""
        if stage == "collector":
            source = artifact["snapshot"]
            self._put("snapshot", dict(source, source=source["source_url"], complete=True, collector_artifact=artifact))
        self._put(stage, artifact)

    def load_compatible(self, stage: str, snapshot_id: str | None = None, model: str | None = None) -> dict | None:
        """Return only stage data matching the current period/snapshot/model contract."""
        artifact = self.artifacts.get(stage)
        if artifact is None and stage == "collector":
            artifact = self.artifacts.get("snapshot", {}).get("collector_artifact")
        if (
            not isinstance(artifact, dict)
            or artifact.get("contract_version") != CONTRACT_VERSION
            or artifact.get("period_id") != self.period
        ):
            return None
        if snapshot_id is not None and artifact.get("snapshot_id") != snapshot_id:
            return None
        if model is not None and artifact.get("model") != model:
            return None
        return artifact

    def _cache_key(self, repository: str, snapshot_id: str, model: str, kind: str) -> str:
        return f"{kind}:{snapshot_id}:{repository}:{model}:{CACHE_PROMPT_VERSION}:{CACHE_SCHEMA_VERSION}"

    def load_analysis(self, repository: str, snapshot_id: str, model: str) -> dict | None:
        """Read a previously validated model response for an exact cache identity."""
        return self.artifacts.get(self._cache_key(repository, snapshot_id, model, "analysis"))

    def save_analysis(self, repository: str, snapshot_id: str, model: str, analysis: dict) -> None:
        """Commit a validated response before continuing to later candidates."""
        self._put(self._cache_key(repository, snapshot_id, model, "analysis"), analysis)
