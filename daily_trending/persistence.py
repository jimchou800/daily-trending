"""Durable per-period artifacts and resumable run state."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .contracts import CONTRACT_VERSION, require_date

MAX_RETRIES = 2
ARTIFACT_SCHEMA_VERSION = "1"
ANALYSIS_SCHEMA_VERSION = "1"


class ArtifactStore:
    """Persist stage artifacts and internal retry/cost state for one report date."""

    def __init__(self, root: Path, period_id: str) -> None:
        require_date(period_id, "period")
        self.directory = Path(root) / period_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / "run-state.json"
        self.state = self._read_state()

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"period_id": self.directory.name, "attempts": 0, "run_count": 0, "status": "pending", "failures": [], "estimated_spend_cny": 0.0, "artifact_schema": ARTIFACT_SCHEMA_VERSION}
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
        if artifact.get("stage", {}).get("status") != "succeeded":
            raise ValueError("only succeeded artifacts may be cached")
        path = self.directory / f"{stage}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return path

    def load_compatible(self, stage: str, snapshot_id: str | None = None, model: str | None = None) -> dict[str, Any] | None:
        """Load a valid cached stage, rejecting corruption, wrong stage, period, or snapshot."""
        path = self.directory / f"{stage}.json"
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
            current = artifact.get("stage", {})
            if artifact.get("contract_version") != CONTRACT_VERSION or artifact.get("period_id") != self.directory.name:
                return None
            if current.get("name") != stage or current.get("status") != "succeeded":
                return None
            if snapshot_id is not None and artifact.get("snapshot_id") != snapshot_id:
                return None
            if model is not None and artifact.get("model") != model:
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
        self.state["status"] = "running"
        self._write_state()

    def finish_run(self, status: str, failure_reason: str | None = None) -> None:
        """Persist completion status and an optional internal failure reason."""
        self.state["status"] = status
        if failure_reason:
            self.state.setdefault("failures", []).append(failure_reason)
        self._write_state()

    def update_usage(self, calls_used: int, estimated_spend_cny: float) -> None:
        """Persist model usage so a resumed process retains budget continuity."""
        self.state["calls_used"] = calls_used
        self.state["estimated_spend_cny"] = estimated_spend_cny
        self._write_state()

    def load_analysis(self, repository: str, snapshot_id: str, model: str) -> dict[str, Any] | None:
        """Load a candidate analysis only when all compatibility identities match."""
        path = self.directory / "analysis-cache.json"
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
            item = cache.get(repository)
            if item and item.get("contract_version") == CONTRACT_VERSION and item.get("schema") == ANALYSIS_SCHEMA_VERSION and item.get("snapshot_id") == snapshot_id and item.get("model") == model:
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
        cache[repository] = {"contract_version": CONTRACT_VERSION, "schema": ANALYSIS_SCHEMA_VERSION, "snapshot_id": snapshot_id, "model": model, "analysis": analysis}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
