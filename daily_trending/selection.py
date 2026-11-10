"""Ordered filtering against successful report history."""

from __future__ import annotations

from .contracts import RepeatBasis, SelectionReason, Status

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path

from .contracts import MISSING_METRIC, require, require_date

BEIJING = timezone(timedelta(hours=8))
WINDOW = timedelta(hours=168)


def load_published_history(site_root: Path) -> list[dict]:
    """Read valid published issue JSON files from a static site's archive."""
    reports: list[dict] = []
    for path in (Path(site_root) / "archive").glob("*/report.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(report, dict)
            and report.get("status") == Status.PUBLISHED
            and report.get("report_date") == path.parent.name
        ):
            reports.append(report)
    return reports


def _period_start(value: str) -> datetime:
    require_date(value, "report_date")
    return datetime.combine(date.fromisoformat(value), time(9), BEIJING)


def _successful_history(history: list[dict]) -> list[dict]:
    return sorted(
        (
            record
            for record in history
            if isinstance(record, dict)
            and record.get("status") == Status.PUBLISHED
            and isinstance(record.get("report_date"), str)
        ),
        key=lambda record: record["report_date"],
        reverse=True,
    )


def _entry_repository(entry: dict) -> str | None:
    return entry.get("repository") if isinstance(entry, dict) else None


def _heat(entry: dict) -> dict:
    value = entry.get("heat") if isinstance(entry, dict) else None
    if isinstance(value, dict):
        return value
    return entry if isinstance(entry, dict) else {}


def _formal_release_after(releases: list[dict], previous: datetime, repository: str) -> dict | None:
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
            continue
        if not isinstance(release.get("tag_name"), str) or not release["tag_name"].strip():
            continue
        if not isinstance(release.get("html_url"), str) or not release["html_url"].startswith(
            f"https://github.com/{repository}/releases/"
        ):
            continue
        published_at = release.get("published_at")
        if not isinstance(published_at, str):
            continue
        try:
            timestamp = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=BEIJING)
        if timestamp.astimezone(BEIJING) > previous:
            return release
    return None


def filter_repeat_entries(
    candidates: list[dict],
    report_date: str,
    history: list[dict],
    *,
    releases_by_repository: dict[str, list[dict]] | None = None,
    limit: int = 5,
) -> tuple[list[dict], list[dict]]:
    """Filter candidates in source order using successful 09:00 issue periods.

    Returns selected copies and an internal decision for every candidate. A
    repeat inside 168 hours is accepted only for a new formal release or a
    strictly doubled, non-zero same-day Star metric.
    """
    require_date(report_date, "report_date")
    require(isinstance(candidates, list), "candidates must be a list")
    require(isinstance(history, list), "history must be a list")
    require(isinstance(limit, int) and limit >= 0, "limit must be non-negative")
    current = _period_start(report_date)
    latest_by_repo: dict[str, dict] = {}
    for record in _successful_history(history):
        for entry in record.get("entries", []):
            repository = _entry_repository(entry)
            if repository and repository not in latest_by_repo:
                latest_by_repo[repository] = {
                    "record": record,
                    "entry": entry,
                    "period": _period_start(record["report_date"]),
                }

    selected: list[dict] = []
    decisions: list[dict] = []
    releases_by_repository = releases_by_repository or {}
    for candidate in candidates:
        repository = _entry_repository(candidate)
        decision: dict = {"repository": repository, "eligible": False}
        if not repository:
            decision["reason"] = SelectionReason.INVALID_CANDIDATE
            decisions.append(decision)
            continue
        previous = latest_by_repo.get(repository)
        if previous is None:
            decision.update(eligible=True, reason=SelectionReason.FIRST_INCLUSION)
        else:
            elapsed = current - previous["period"]
            if elapsed >= WINDOW:
                decision.update(eligible=True, reason=SelectionReason.WINDOW_ELAPSED)
            else:
                old_stars = _heat(previous["entry"]).get("stars_today")
                new_stars = _heat(candidate).get("stars_today")
                doubled = (
                    isinstance(old_stars, int)
                    and old_stars > 0
                    and isinstance(new_stars, int)
                    and new_stars >= old_stars * 2
                    and new_stars > old_stars
                )
                released = _formal_release_after(
                    releases_by_repository.get(repository, []), previous["period"], repository
                )
                if released:
                    decision.update(
                        eligible=True,
                        reason=SelectionReason.RECENT_EXCEPTION,
                        repeat_basis=RepeatBasis.NEW_FORMAL_RELEASE,
                        release=released,
                    )
                elif doubled:
                    decision.update(
                        eligible=True, reason=SelectionReason.RECENT_EXCEPTION, repeat_basis=RepeatBasis.STARS_DOUBLED
                    )
                else:
                    decision["reason"] = SelectionReason.RECENT_DUPLICATE
        if decision["eligible"]:
            enriched = deepcopy(candidate)
            if previous is not None:
                enriched["repeat_basis"] = decision.get("repeat_basis", SelectionReason.WINDOW_ELAPSED)
                old_stars = _heat(previous["entry"]).get("stars_today")
                new_stars = _heat(candidate).get("stars_today")
                comparison = f"当日新增 Star {old_stars if old_stars is not None else MISSING_METRIC} → {new_stars if new_stars is not None else MISSING_METRIC}"
                release = decision.get("release")
                if release:
                    comparison = f"新增正式 Release {release.get('tag_name') or '（未提供标签）'}，发布时间 {release['published_at']}；{comparison}"
                    if release.get("html_url"):
                        enriched.setdefault("sources", []).append(release["html_url"])
                enriched["repeat_change"] = f"相较 {previous['record']['report_date']}：{comparison}"
                enriched["previous_report_url"] = f"/archive/{previous['record']['report_date']}/"
                decision["previous_report_url"] = enriched["previous_report_url"]
            if len(selected) < limit:
                selected.append(enriched)
            else:
                decision["eligible"] = False
                decision["reason"] = SelectionReason.LIMIT_REACHED
        decisions.append(decision)
    return selected, decisions
