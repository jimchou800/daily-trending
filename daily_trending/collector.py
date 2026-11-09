"""Collector stage for validated, saved GitHub Trending snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import (
    CONTRACT_VERSION,
    TRENDING_SCOPE,
    TRENDING_URL,
    require,
    require_date,
    require_github_url,
    require_timestamp,
)


def collect_snapshot(snapshot_path: Path, issue_date: str, collected_at: str) -> dict:
    """Load a complete saved Daily snapshot and produce a collector artifact.

    The source must declare a complete ordered snapshot.  The returned artifact
    owns the collection timestamp and deterministic snapshot identity used by
    downstream stages.
    """
    require_date(issue_date, "period")
    require_timestamp(collected_at, "collected_at")
    try:
        payload = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("snapshot could not be read") from exc
    require(payload.get("complete") is True, "snapshot is not complete")
    entries = payload.get("entries")
    require(isinstance(entries, list) and entries, "snapshot entries are missing")
    normalized = []
    for index, entry in enumerate(entries, start=1):
        require(isinstance(entry, dict), "snapshot entry is invalid")
        require(entry.get("rank") == index, "snapshot ranks must preserve source order")
        repository = entry.get("repository")
        repository_url = entry.get("repository_url")
        require(isinstance(repository, str) and repository, "snapshot repository is missing")
        require_github_url(repository_url, "repository")
        require(repository_url.rstrip("/").endswith(repository), "repository link does not match identifier")
        for metric in ("stars_today", "stars_total"):
            require(entry.get(metric) is None or isinstance(entry.get(metric), int), f"invalid {metric}")
        normalized.append(dict(entry))
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return {
        "contract_version": CONTRACT_VERSION,
        "period_id": issue_date,
        "snapshot_id": snapshot_id,
        "stage": {"name": "collector", "status": "succeeded", "errors": []},
        "snapshot": {
            "source_url": TRENDING_URL,
            "scope": TRENDING_SCOPE,
            "collected_at": collected_at,
            "entries": normalized,
        },
    }
