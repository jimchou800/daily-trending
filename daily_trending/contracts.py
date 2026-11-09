"""Shared contract validation and business constants."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

CONTRACT_VERSION = "1.0"
TRENDING_URL = "https://github.com/trending?since=daily"
TRENDING_SCOPE = "GitHub Trending Daily（全部编程语言）"
UNKNOWN_REASON = "暂未找到明确原因"
MISSING_METRIC = "未获取"


def require(condition: bool, message: str) -> None:
    """Raise ``ValueError`` when a contract condition is false."""
    if not condition:
        raise ValueError(message)


def require_date(value: str, field: str) -> None:
    """Validate an ISO calendar date used as a period identifier."""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc


def require_timestamp(value: str, field: str) -> None:
    """Validate an ISO timestamp, accepting a trailing UTC ``Z``."""
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc


def require_github_url(value: str, field: str = "source") -> None:
    """Ensure a required source URL points to GitHub over HTTPS."""
    parsed = urlparse(value)
    require(parsed.scheme == "https" and parsed.netloc == "github.com", f"invalid {field} link")


def validate_common_artifact(artifact: dict, expected_stage: str) -> None:
    """Validate identifiers and stage status shared by pipeline artifacts."""
    require(isinstance(artifact, dict), "artifact must be an object")
    require(artifact.get("contract_version") == CONTRACT_VERSION, "unsupported contract version")
    require(isinstance(artifact.get("period_id"), str), "missing period")
    require_date(artifact["period_id"], "period")
    require(isinstance(artifact.get("snapshot_id"), str) and artifact["snapshot_id"], "missing snapshot")
    stage = artifact.get("stage")
    require(isinstance(stage, dict) and stage.get("name") == expected_stage, f"invalid {expected_stage} stage")
    require(stage.get("status") == "succeeded", f"{expected_stage} stage is not succeeded")
