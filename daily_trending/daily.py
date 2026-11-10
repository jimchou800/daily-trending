"""Run one bounded daily generation and publish it to a static site directory."""

from __future__ import annotations

from .contracts import Status

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from daily_trending.dashscope import DashScopeClient
from daily_trending.github_sources import load_github_sources
from daily_trending.organizer import render_html
from daily_trending.pipeline import run_live_pipeline
from daily_trending.publisher import publish_report, recover_publication
from daily_trending.scheduler import BEIJING, BeijingScheduler, render_homepage, run_with_retries


def _initialize_site(site_root: Path) -> None:
    """Create a truthful first-visit page and empty archive index if needed."""
    site_root.mkdir(parents=True, exist_ok=True)
    index_path = site_root / "index.json"
    if not index_path.exists():
        index_path.write_text(
            json.dumps({"latest_report_date": None, "published_at": None, "archive_dates": []}, indent=2) + "\n",
            encoding="utf-8",
        )
    homepage = site_root / "index.html"
    if not homepage.exists():
        homepage.write_text(
            render_homepage(
                '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                "<title>AI 开源项目热点日报</title><body><main><h1>AI 开源项目热点日报</h1>"
                "<p>尚未发布</p></main></body></html>"
            ),
            encoding="utf-8",
        )


def run_daily(
    site_root: Path,
    state_root: Path,
    *,
    clock: Callable[[], datetime] | None = None,
    client: DashScopeClient | None = None,
    fetch: Callable[[str], str] | None = None,
    source_loader: Callable[[dict], dict] | None = None,
) -> Path:
    """Run one local publication using durable state and an exclusive process lock.

    The site directory must be the authoritative publication destination, not a
    staging directory for a later deployment. External adapters and the clock
    are injectable for integration verification. State loss and overlapping
    runs fail closed; an interrupted process lock requires operator recovery.
    """
    clock = clock or (lambda: datetime.now(BEIJING))
    current = clock()
    if current.tzinfo is None:
        raise ValueError("daily clock must be timezone-aware")
    report_date = current.astimezone(BEIJING).date().isoformat()
    scheduler = BeijingScheduler(report_date, now=clock)

    def check_deadline() -> None:
        if not scheduler.before_deadline():
            raise TimeoutError("report publication deadline has passed")

    state_root.mkdir(parents=True, exist_ok=True)
    marker = state_root / "initialized"
    if not marker.exists() and (site_root / "archive").exists():
        raise ValueError("durable state is missing for an existing site")
    lock = state_root / "run.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise RuntimeError("daily run is already active or needs interruption recovery") from exc
    client = client or DashScopeClient()
    original_guard = client.before_call

    def guarded_call() -> None:
        check_deadline()
        if original_guard:
            original_guard()

    client.before_call = guarded_call

    def guarded_sources(entry: dict) -> dict:
        check_deadline()
        return (source_loader or load_github_sources)(entry)

    def attempt(_attempt_number: int) -> Path:
        check_deadline()
        recovered = recover_publication(site_root, now=clock())
        if recovered is not None and recovered.parent.name == report_date:
            return recovered
        collected_at = clock().astimezone(timezone.utc).isoformat(timespec="seconds")
        usage_marker = state_root / "usage-month"
        temporary_marker = usage_marker.with_suffix(".tmp")
        temporary_marker.write_text(report_date[:7], encoding="utf-8")
        temporary_marker.replace(usage_marker)
        result = run_live_pipeline(
            state_root / "output",
            report_date,
            collected_at,
            client,
            artifact_root=state_root / "artifacts",
            history_root=site_root,
            fetch=fetch,
            source_loader=guarded_sources,
        )
        check_deadline()
        report = json.loads(result.report_path.read_text(encoding="utf-8"))
        report["published_at"] = clock().astimezone(timezone.utc).isoformat(timespec="seconds")
        report["status"] = Status.PUBLISHED
        return publish_report(
            report,
            render_html(report),
            site_root,
            report["published_at"],
            now=clock(),
        )

    try:
        recover_publication(site_root, now=clock())
        check_deadline()
        usage_month = state_root / "usage-month"
        if usage_month.exists():
            month = usage_month.read_text(encoding="utf-8").strip()
            if len(month) != 7 or datetime.strptime(month, "%Y-%m").strftime("%Y-%m") != month:
                raise ValueError("durable usage month is corrupt")
            if month == report_date[:7] and not (state_root / "artifacts" / f"monthly-{month}.json").exists():
                raise ValueError("durable monthly usage ledger is missing")
        marker.touch(exist_ok=True)
        _initialize_site(site_root)
        existing = site_root / "archive" / report_date / "report.json"
        if existing.exists():
            return existing
        return run_with_retries(scheduler, attempt)
    finally:
        client.before_call = original_guard
        lock.rmdir()


def main() -> int:
    """Run the local job using the configured conventional state/site directories."""
    try:
        target = run_daily(Path("site"), Path(".daily-trending-state"))
        print(f"Published {target}")
        return 0
    except Exception as exc:
        print(f"Daily publication failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
