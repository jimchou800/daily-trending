"""Durable per-period artifacts and resumable run state."""

from __future__ import annotations

from .contracts import Stage, Status, UsageStatus

import json
import math
import os
from pathlib import Path
from typing import Any

from .contracts import CONTRACT_VERSION, require_date

MAX_RETRIES = 2
ARTIFACT_SCHEMA_VERSION = "1"
ANALYSIS_SCHEMA_VERSION = "2"


class ArtifactStore:
    """Persist stage artifacts and internal retry/cost state for one report date."""

    def __init__(self, root: Path, period_id: str) -> None:
        require_date(period_id, "period")
        self.root = Path(root)
        self.directory = self.root / period_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / "run-state.json"
        self.state = self._read_state()

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "period_id": self.directory.name,
                "attempts": 0,
                "run_count": 0,
                "status": Status.PENDING,
                "failures": [],
                "estimated_spend_cny": 0.0,
                "artifact_schema": ARTIFACT_SCHEMA_VERSION,
            }
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("period_id") != self.directory.name:
                raise ValueError("state period mismatch")
            if state.get("artifact_schema", ARTIFACT_SCHEMA_VERSION) != ARTIFACT_SCHEMA_VERSION:
                raise ValueError("unsupported state schema")
            return state
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("run state is corrupt") from exc

    def _write_state(self) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.state_path)

    def save(self, stage: str, artifact: dict[str, Any]) -> Path:
        """Atomically save a complete stage artifact after basic identity checks."""
        if artifact.get("contract_version") != CONTRACT_VERSION or artifact.get("period_id") != self.directory.name:
            raise ValueError("artifact identity mismatch")
        if artifact.get("stage", {}).get("name") != stage:
            raise ValueError("artifact stage mismatch")
        if artifact.get("stage", {}).get("status") != Status.SUCCEEDED:
            raise ValueError("only succeeded artifacts may be cached")
        path = self.directory / f"{stage}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return path

    def load_compatible(
        self, stage: str, snapshot_id: str | None = None, model: str | None = None
    ) -> dict[str, Any] | None:
        """Load a valid cached stage, rejecting corruption, wrong stage, period, or snapshot."""
        path = self.directory / f"{stage}.json"
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
            current = artifact.get("stage", {})
            if artifact.get("contract_version") != CONTRACT_VERSION or artifact.get("period_id") != self.directory.name:
                return None
            if current.get("name") != stage or current.get("status") != Status.SUCCEEDED:
                return None
            if snapshot_id is not None and artifact.get("snapshot_id") != snapshot_id:
                return None
            if model is not None and artifact.get("model") != model:
                return None
            if stage == Stage.ANALYZER and artifact.get("analysis_schema") != ANALYSIS_SCHEMA_VERSION:
                return None
            return artifact
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            return None

    def record_attempt(self, status: str, failure_reason: str | None = None, *, cost_cny: float = 0.0) -> None:
        """Record a run attempt without resetting counters after interruption or restart."""
        if self.state["attempts"] >= MAX_RETRIES:
            raise RuntimeError("retry limit exhausted")
        self.state["attempts"] += 1
        self.state["status"] = status
        self.state["estimated_spend_cny"] = round(float(self.state.get("estimated_spend_cny", 0.0)) + cost_cny, 6)
        if failure_reason:
            self.state.setdefault("failures", []).append(failure_reason)
        self._write_state()

    def begin_run(self) -> None:
        """Reserve one run attempt, preserving the two-retry limit across restarts."""
        if self.state.get("run_count", 0) >= MAX_RETRIES + 1:
            raise RuntimeError("retry limit exhausted")
        self.state["run_count"] = self.state.get("run_count", 0) + 1
        self.state["status"] = Status.RUNNING
        self._write_state()

    def finish_run(self, status: str, failure_reason: str | None = None) -> None:
        """Persist completion status and an optional internal failure reason."""
        self.state["status"] = status
        if failure_reason:
            self.state.setdefault("failures", []).append(failure_reason)
        self._write_state()

    def update_usage(
        self,
        calls_used: int,
        estimated_spend_cny: float,
        *,
        monthly_calls_used: int | None = None,
        period_spend_cny: float | None = None,
    ) -> None:
        """Reserve monthly usage before period state; a failed second write stays charged.

        Calls are period-scoped unless the separate monthly total is supplied.
        Spend is the monthly total, with a separate period total when available.
        The caller must serialize updates across all report dates.
        """
        self.state["calls_used"] = calls_used
        self.state["estimated_spend_cny"] = estimated_spend_cny if period_spend_cny is None else period_spend_cny
        monthly_path = self.root / f"monthly-{self.directory.name[:7]}.json"
        try:
            prior = json.loads(monthly_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            prior = {}
        temporary = monthly_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "calls_used": calls_used if monthly_calls_used is None else monthly_calls_used,
                    "estimated_spend_cny": estimated_spend_cny,
                    "accounting_blocked": prior.get("accounting_blocked", False),
                    "usage_records": prior.get("usage_records", []),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, monthly_path)
        self._write_state()

    def monthly_usage(self) -> dict[str, float | int]:
        """Return cumulative model usage for this report's calendar month."""
        path = self.root / f"monthly-{self.directory.name[:7]}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("monthly usage must be an object")
            calls_used = data.get("calls_used")
            spend = data.get("estimated_spend_cny")
            if (
                type(calls_used) is not int
                or calls_used < 0
                or type(spend) not in (int, float)
                or not math.isfinite(spend)
                or spend < 0
            ):
                raise ValueError("invalid monthly usage")
            if type(data.get("accounting_blocked", False)) is not bool:
                raise ValueError("invalid accounting block")
            records = data.get("usage_records", [])
            if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
                raise ValueError("invalid usage records")
            allowed_fields = {
                "reserved_cost_cny",
                "usage_status",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "actual_cost_cny",
            }
            for record in records:
                if set(record) - allowed_fields or record.get("usage_status") not in (
                    UsageStatus.UNAVAILABLE,
                    UsageStatus.REPORTED,
                ):
                    raise ValueError("invalid usage record fields")
                for key, value in record.items():
                    if key != "usage_status" and (
                        type(value) not in (int, float) or not math.isfinite(value) or value < 0
                    ):
                        raise ValueError("invalid usage record value")
            result = {"calls_used": calls_used, "estimated_spend_cny": spend}
            if data.get("accounting_blocked"):
                result["accounting_blocked"] = True
            if data.get("usage_records"):
                result["usage_records"] = data["usage_records"]
            return result
        except FileNotFoundError:
            if any(self.root.glob(f"{self.directory.name[:7]}-*/run-state.json")):
                raise ValueError("monthly usage ledger is missing for existing run state")
            return {"calls_used": 0, "estimated_spend_cny": 0.0}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("monthly usage ledger is corrupt") from exc

    def update_accounting(self, blocked: bool, records: list[dict]) -> None:
        """Persist provider accounting evidence and a latched monthly spending stop.

        The monthly ledger is authoritative across report dates; an interrupted
        period-state write must not release an accounting block.
        """
        usage = self.monthly_usage()
        usage["accounting_blocked"] = bool(usage.get("accounting_blocked")) or blocked
        usage["usage_records"] = records
        path = self.root / f"monthly-{self.directory.name[:7]}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        self.state["accounting_blocked"] = usage["accounting_blocked"]
        self.state["usage_records"] = records
        self._write_state()

    def load_analysis(self, repository: str, snapshot_id: str, model: str) -> dict[str, Any] | None:
        """Load a candidate analysis only when all compatibility identities match."""
        path = self.directory / "analysis-cache.json"
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
            item = cache.get(repository)
            if (
                item
                and item.get("contract_version") == CONTRACT_VERSION
                and item.get("schema") == ANALYSIS_SCHEMA_VERSION
                and item.get("snapshot_id") == snapshot_id
                and item.get("model") == model
            ):
                analysis = item.get("analysis")
                if isinstance(analysis, dict) and analysis.get("repository") == repository:
                    return analysis
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            return None
        return None

    def save_analysis(self, repository: str, snapshot_id: str, model: str, analysis: dict[str, Any]) -> None:
        """Persist one validated candidate response for reuse on a resumed run."""
        path = self.directory / "analysis-cache.json"
        try:
            cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError, json.JSONDecodeError):
            cache = {}
        cache[repository] = {
            "contract_version": CONTRACT_VERSION,
            "schema": ANALYSIS_SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "model": model,
            "analysis": analysis,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
