"""Shared contract validation and business constants."""

from __future__ import annotations

from enum import StrEnum
from datetime import datetime
from urllib.parse import urlparse

CONTRACT_VERSION = "1.0"
TRENDING_URL = "https://github.com/trending?since=daily"
TRENDING_SCOPE = "GitHub Trending Daily（全部编程语言）"
UNKNOWN_REASON = "暂未找到明确原因"
MISSING_METRIC = "未获取"


class Stage(StrEnum):
    """Canonical Stage values shared by producers and validators."""

    COLLECTOR = "collector"
    ANALYZER = "analyzer"
    ORGANIZER = "organizer"
    PUBLISHER = "publisher"
    SELECTION = "selection"


class Status(StrEnum):
    """Canonical Status values shared by producers and validators."""

    PUBLISHED = "published"
    SAMPLE_PREVIEW = "sample_preview"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PENDING = "pending"
    RUNNING = "running"
    UNPUBLISHED = "unpublished"
    EXPIRED = "expired"
    BUDGET_EXHAUSTED = "budget_exhausted"
    RETRY_EXHAUSTED = "retry_exhausted"
    READY = "ready"


class SelectionOutcome(StrEnum):
    """Canonical SelectionOutcome values shared by producers and validators."""

    SELECTED = "selected"
    EXCLUDED = "excluded"
    ANALYSIS_FAILED = "analysis_failed"


class SelectionReason(StrEnum):
    """Canonical SelectionReason values shared by producers and validators."""

    INVALID_CANDIDATE = "invalid_candidate"
    FIRST_INCLUSION = "first_inclusion"
    WINDOW_ELAPSED = "window_elapsed"
    RECENT_EXCEPTION = "recent_exception"
    RECENT_DUPLICATE = "recent_duplicate"
    LIMIT_REACHED = "limit_reached"
    NOT_AI = "not_ai"
    UNKNOWN_RELEVANCE = "unknown_relevance"
    CLASSIFICATION_INCOMPLETE = "classification_incomplete"


class RepeatBasis(StrEnum):
    """Canonical RepeatBasis values shared by producers and validators."""

    NEW_FORMAL_RELEASE = "new_formal_release"
    STARS_DOUBLED = "stars_doubled"


class UsageStatus(StrEnum):
    """Canonical UsageStatus values shared by producers and validators."""

    UNAVAILABLE = "unavailable"
    REPORTED = "reported"


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
    require(stage.get("status") == Status.SUCCEEDED, f"{expected_stage} stage is not succeeded")


def validate_public_report(report: dict) -> None:
    """Validate the canonical JSON contract before a report becomes public."""
    require(isinstance(report, dict), "report must be an object")
    for field in (
        "contract_version",
        "report_date",
        "timezone",
        "collected_at",
        "source",
        "scope",
        "status",
        "entry_count",
        "entries",
    ):
        require(field in report, f"missing report field: {field}")
    require(report["contract_version"] == CONTRACT_VERSION, "unsupported report contract version")
    require_date(report["report_date"], "report_date")
    require_timestamp(report["collected_at"], "collected_at")
    require_github_url(report["source"], "source")
    require(report["timezone"] == "Asia/Shanghai", "invalid report timezone")
    require(report["status"] in (Status.PUBLISHED, Status.SAMPLE_PREVIEW), "invalid report status")
    entries = report["entries"]
    require(isinstance(entries, list) and len(entries) <= 5, "invalid report entries")
    require(report["entry_count"] == len(entries), "invalid report entry count")
    for entry in entries:
        require(isinstance(entry, dict), "invalid report entry")
        for field in (
            "repository",
            "repository_url",
            "rank",
            "ai_relevance_evidence",
            "project_type",
            "introduction",
            "problem_solved",
            "audience",
            "characteristics",
            "heat",
            "sources",
        ):
            require(field in entry, f"missing report entry field: {field}")
        require_github_url(entry["repository_url"], "repository")
        require(isinstance(entry["sources"], list) and entry["sources"], "analysis sources are missing")
        for source in entry["sources"]:
            require_github_url(source, "analysis source")
        heat = entry["heat"]
        require(isinstance(heat, dict), "invalid heat metrics")
        require(heat.get("stars_today") is None or isinstance(heat.get("stars_today"), int), "invalid stars_today")
        require(heat.get("stars_total") is None or isinstance(heat.get("stars_total"), int), "invalid stars_total")
