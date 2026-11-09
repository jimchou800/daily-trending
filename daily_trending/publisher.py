"""Atomic static publication and successful-report history."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from .contracts import require, require_date, require_github_url, require_timestamp
from .organizer import render_html
from .scheduler import render_homepage

TRANSACTION_FILE = ".publication-transaction.json"


def load_successful_history(site_root: Path) -> list[dict]:
    """Load successfully published reports in descending report-date order."""
    root = Path(site_root)
    history_path = root / "history.json"
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict) and item.get("status") == "published"]
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    reports = []
    for path in (root / "archive").glob("*/report.json") if (root / "archive").exists() else []:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("status") == "published":
                reports.append(report)
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            continue
    return sorted(reports, key=lambda item: item.get("report_date", ""), reverse=True)


def _write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _recover_publication(root: Path) -> None:
    """Complete a publication left between atomic file replacements."""
    manifest_path = root / TRANSACTION_FILE
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = manifest["report"]
    archive = root / "archive" / report["report_date"]
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "index.html").write_text(manifest["html"], encoding="utf-8")
    _write_json_atomic(archive / "report.json", report)
    index_path = root / "index.json"
    try:
        current_index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        current_index = {}
    is_current = current_index.get("latest_report_date", "") <= report["report_date"]
    if is_current:
        _write_json_atomic(index_path, manifest["index"])
        (root / "index.html.tmp").write_text(manifest["homepage"], encoding="utf-8")
        os.replace(root / "index.html.tmp", root / "index.html")
    history_by_date = {item.get("report_date"): item for item in load_successful_history(root) if item.get("report_date")}
    history_by_date.setdefault(report["report_date"], report)
    merged_history = sorted(history_by_date.values(), key=lambda item: item.get("report_date", ""), reverse=True)
    _write_json_atomic(root / "history.json", merged_history)
    manifest_path.unlink(missing_ok=True)

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
    _recover_publication(root)
    archive = root / "archive" / report["report_date"]
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / "report.json"
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        report["published_at"] = existing.get("published_at", published_at)
    index = root / "index.json"
    current = json.loads(index.read_text(encoding="utf-8")) if index.exists() else {}
    if current.get("latest_report_date", "") > report["report_date"]:
        return target
    current["latest_report_date"] = report["report_date"]
    current["published_at"] = report["published_at"]
    current["archive_dates"] = sorted(set(current.get("archive_dates", [])) | {report["report_date"]}, reverse=True)
    history = load_successful_history(root)
    if not any(item.get("report_date") == report["report_date"] for item in history):
        history.append(report)
    history.sort(key=lambda item: item.get("report_date", ""), reverse=True)
    manifest = {"report": report, "html": html_content, "homepage": render_homepage(html_content), "index": current, "history": history}
    _write_json_atomic(root / TRANSACTION_FILE, manifest)
    _recover_publication(root)
    return target


def correct_report(
    corrected_report: dict,
    site_root: Path,
    corrected_at: str,
    note: str,
) -> Path:
    """Replace facts in an existing report while preserving its identity and history.

    The report date, source, publication timestamp, selected entries, and history
    are immutable.  A correction is recorded on the same archive path and the
    latest homepage is updated only when it already exists.
    """
    require(isinstance(corrected_report, dict), "report must be an object")
    require_timestamp(corrected_at, "corrected_at")
    require(isinstance(note, str) and note.strip(), "correction note is required")
    date = corrected_report.get("report_date")
    require_date(date, "report_date")
    root = Path(site_root)
    archive = root / "archive" / date
    target = archive / "report.json"
    page = archive / "index.html"
    require(target.exists() and page.exists(), "published report does not exist")
    existing = json.loads(target.read_text(encoding="utf-8"))
    require(existing.get("status") == "published", "only published reports can be corrected")
    require_github_url(existing.get("source", existing.get("source_url", "https://github.com/trending?since=daily")), "source")
    for field in ("report_date", "source", "source_url", "history", "snapshot_id"):
        if field in existing or field in corrected_report:
            require(corrected_report.get(field) == existing.get(field), f"immutable field: {field}")
    require(corrected_report.get("published_at", existing.get("published_at")) == existing.get("published_at"), "immutable field: published_at")
    require(corrected_report.get("status", existing.get("status")) == "published", "immutable field: status")
    entries = existing.get("entries")
    replacement_entries = corrected_report.get("entries", entries)
    require(isinstance(entries, list) and isinstance(replacement_entries, list) and len(entries) == len(replacement_entries), "immutable selection")
    for before, after in zip(entries, replacement_entries):
        require(isinstance(before, dict) and isinstance(after, dict), "invalid report entry")
        required = ("repository", "repository_url", "rank", "ai_relevance_evidence", "project_type", "introduction", "problem_solved", "audience", "characteristics", "heat", "sources")
        require(all(field in after for field in required), "analysis entry is incomplete")
        for field in ("repository", "repository_url", "rank", "heat"):
            require(after.get(field) == before.get(field), f"immutable selection field: {field}")
        require_github_url(after.get("repository_url", ""), "repository")
        sources = after.get("sources")
        require(isinstance(sources, list) and bool(sources), "analysis sources are missing")
        for source in sources:
            require_github_url(source, "analysis source")
        heat = after.get("heat")
        require(isinstance(heat, dict), "invalid heat metrics")
        require(heat.get("stars_today") is None or isinstance(heat.get("stars_today"), int), "invalid stars_today")
        require(heat.get("stars_total") is None or isinstance(heat.get("stars_total"), int), "invalid stars_total")
    updated = dict(existing)
    for key, value in corrected_report.items():
        if key not in {"report_date", "source", "source_url", "history", "snapshot_id", "entries", "published_at", "status", "correction", "corrections"}:
            updated[key] = value
    if "entries" in corrected_report:
        updated["entries"] = replacement_entries
    correction = {"corrected_at": corrected_at, "note": note.strip()}
    corrections = list(existing.get("corrections", []))
    if existing.get("correction"):
        corrections.append(existing["correction"])
    corrections.append(correction)
    updated["corrections"] = corrections
    updated["correction"] = correction

    require(updated.get("entry_count") == len(updated["entries"]), "invalid report entry count")
    require_timestamp(updated.get("collected_at"), "collected_at")
    require_github_url(updated.get("source", ""), "source")
    html_content = render_html(updated)
    correction_banner = (
        '<aside class="correction"><strong>内容更正</strong> · '
        f'{html_escape(corrected_at)} · {html_escape(note.strip())}</aside>'
    )
    require(re.search(r"</main\s*>", html_content, flags=re.IGNORECASE) is not None, "corrected page must contain main")
    html_content = re.sub(r"</main\s*>", correction_banner + "</main>", html_content, count=1, flags=re.IGNORECASE)
    temporary_json = target.with_suffix(".correction.tmp")
    temporary_html = page.with_suffix(".correction.tmp")
    temporary_json.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_html.write_text(html_content, encoding="utf-8")
    homepage = root / "index.html"
    index_path = root / "index.json"
    index_data = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    is_latest = index_data.get("latest_report_date") == date
    temporary_home = homepage.with_suffix(".correction.tmp") if is_latest and homepage.exists() else None
    if temporary_home:
        temporary_home.write_text(render_homepage(html_content), encoding="utf-8")
    old_json = target.read_bytes()
    old_html = page.read_bytes()
    old_home = homepage.read_bytes() if temporary_home else None
    try:
        os.replace(temporary_json, target)
        os.replace(temporary_html, page)
        if temporary_home:
            os.replace(temporary_home, homepage)
    except Exception:
        target.write_bytes(old_json)
        page.write_bytes(old_html)
        if old_home is not None:
            homepage.write_bytes(old_home)
        for temporary in (temporary_json, temporary_html, temporary_home):
            if temporary and temporary.exists():
                temporary.unlink()
        raise
    return target


def html_escape(value: str) -> str:
    """Escape correction metadata before embedding it in a static page."""
    import html

    return html.escape(value, quote=True)
