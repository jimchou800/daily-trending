"""Atomic static publication and successful-report history."""

from __future__ import annotations

import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from .contracts import require, require_date, require_timestamp
from .scheduler import render_homepage


def publish_report(report: dict, html_content: str, site_root: Path, published_at: str, *, now: datetime | None = None) -> Path:
    """Publish a report before its Beijing deadline and commit the index last."""
    require_date(report.get("report_date"), "report_date")
    require_timestamp(published_at, "published_at")
    require(report.get("status") in ("published", "sample_preview"), "report is not publishable")
    beijing = timezone(timedelta(hours=8))
    current_time = now.astimezone(beijing) if now else datetime.now(beijing)
    deadline = datetime.combine(datetime.fromisoformat(report["report_date"]).date(), time(9, 0), beijing)
    require(current_time < deadline, "publication deadline has passed")
    report = dict(report)
    report["published_at"] = published_at
    report["status"] = "published"
    root = Path(site_root)
    archive = root / "archive" / report["report_date"]
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / "report.json"
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        report["published_at"] = existing.get("published_at", published_at)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    (archive / "index.html").write_text(html_content, encoding="utf-8")
    index = root / "index.json"
    current = json.loads(index.read_text(encoding="utf-8")) if index.exists() else {}
    if current.get("latest_report_date", "") > report["report_date"]:
        return target
    current["latest_report_date"] = report["report_date"]
    current["published_at"] = report["published_at"]
    current["archive_dates"] = sorted(set(current.get("archive_dates", [])) | {report["report_date"]}, reverse=True)
    temporary_index = index.with_suffix(".tmp")
    temporary_index.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_index, index)
    homepage_temporary = root / "index.html.tmp"
    homepage_temporary.write_text(render_homepage(html_content), encoding="utf-8")
    os.replace(homepage_temporary, root / "index.html")
    return target
