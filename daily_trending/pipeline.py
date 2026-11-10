"""Saved-sample end-to-end pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .analyzer import analyze_live_snapshot, analyze_snapshot
from .collector import collect_snapshot, collect_trending
from .dashscope import DashScopeClient
from .organizer import organize_report, write_report
from .persistence import ArtifactStore
from .selection import filter_repeat_entries, load_published_history


@dataclass(frozen=True)
class PipelineResult:
    """Paths to generated canonical JSON and static HTML artifacts."""

    report_path: Path
    html_path: Path
    collector_path: Path
    analyzer_path: Path


def run_sample_pipeline(
    snapshot_path: Path,
    response_path: Path,
    output_dir: Path,
    issue_date: str,
    collected_at: str,
    *,
    history: list[dict] | None = None,
) -> PipelineResult:
    """Run collector, analyzer, and organizer against saved development samples."""
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = collect_snapshot(snapshot_path, issue_date, collected_at)
    collector_path = output_dir / "collector.json"
    collector_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    analysis = analyze_snapshot(snapshot, response_path, expected_period=issue_date)
    analyzer_path = output_dir / "analyzer.json"
    analyzer_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    analysis["analyses"], decisions = filter_repeat_entries(analysis["analyses"], issue_date, history or [])
    (output_dir / "selection.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = organize_report(analysis)
    report_path, html_path = write_report(report, output_dir)
    return PipelineResult(report_path, html_path, collector_path, analyzer_path)


def run_live_pipeline(
    output_dir: Path,
    issue_date: str,
    collected_at: str,
    client: DashScopeClient,
    *,
    fetch: Callable[[str], str] | None = None,
    source_loader: Callable[[dict], dict] | None = None,
    artifact_root: Path | None = None,
    history: list[dict] | None = None,
    history_root: Path | None = None,
    releases_by_repository: dict[str, list[dict]] | None = None,
) -> PipelineResult:
    """Collect the live Daily snapshot, analyze it, and write a preview artifact."""
    output_dir.mkdir(parents=True, exist_ok=True)
    original_reserve = client._reserve_call
    store = ArtifactStore(artifact_root, issue_date) if artifact_root else None
    if store:
        monthly_usage = store.monthly_usage()
        period_spend = store.state.get("estimated_spend_cny", 0.0)
        store.update_usage(
            store.state.get("calls_used", 0),
            monthly_usage["estimated_spend_cny"],
            monthly_calls_used=monthly_usage["calls_used"],
            period_spend_cny=period_spend,
        )
        store.begin_run()
    try:
        snapshot = store.load_compatible("collector") if store else None
        if snapshot is None:
            snapshot = collect_trending(issue_date, collected_at, fetch=fetch)
            if store:
                store.save("collector", snapshot)
        collector_path = output_dir / "collector.json"
        collector_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if store:
            period_calls = store.state.get("calls_used", 0)
            client.restore_usage(period_calls, monthly_usage["estimated_spend_cny"])
            client.accounting_blocked = bool(monthly_usage.get("accounting_blocked"))
            client.usage_records = list(monthly_usage.get("usage_records", []))

            def reserve_with_state() -> None:
                original_reserve()
                store.update_usage(
                    client.calls_used,
                    client.estimated_spend_cny,
                    monthly_calls_used=monthly_usage["calls_used"] + client.calls_used - period_calls,
                    period_spend_cny=period_spend + client.estimated_spend_cny - monthly_usage["estimated_spend_cny"],
                )

            client._reserve_call = reserve_with_state
        successful_history = (
            history if history is not None else load_published_history(history_root) if history_root else []
        )
        analysis = analyze_live_snapshot(
            snapshot,
            client,
            source_loader=source_loader,
            expected_period=issue_date,
            cache_store=store,
            history=successful_history,
            releases_by_repository=releases_by_repository,
        )
        if store and analysis["stage"]["status"] == "succeeded":
            analysis["model"] = client.config.model
            store.save("analyzer", analysis)
        analyzer_path = output_dir / "analyzer.json"
        analyzer_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (output_dir / "selection.json").write_text(
            json.dumps(analysis["selection"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        report = organize_report(analysis)
        report_path, html_path = write_report(report, output_dir)
        if store:
            store.finish_run("succeeded")
        return PipelineResult(report_path, html_path, collector_path, analyzer_path)
    except Exception as exc:
        if store:
            store.finish_run("failed", str(exc))
        raise
    finally:
        client._reserve_call = original_reserve
        if store:
            store.update_accounting(getattr(client, "accounting_blocked", False), getattr(client, "usage_records", []))
