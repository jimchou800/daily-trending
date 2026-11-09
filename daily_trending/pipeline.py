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


@dataclass(frozen=True)
class PipelineResult:
    """Paths to generated canonical JSON and static HTML artifacts."""

    report_path: Path
    html_path: Path
    collector_path: Path
    analyzer_path: Path


def run_sample_pipeline(snapshot_path: Path, response_path: Path, output_dir: Path, issue_date: str, collected_at: str) -> PipelineResult:
    """Run collector, analyzer, and organizer against saved development samples."""
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = collect_snapshot(snapshot_path, issue_date, collected_at)
    collector_path = output_dir / "collector.json"
    collector_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    analysis = analyze_snapshot(snapshot, response_path, expected_period=issue_date)
    analyzer_path = output_dir / "analyzer.json"
    analyzer_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
) -> PipelineResult:
    """Collect the live Daily snapshot, analyze it, and write a preview artifact."""
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = collect_trending(issue_date, collected_at, fetch=fetch)
    collector_path = output_dir / "collector.json"
    collector_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    analysis = analyze_live_snapshot(snapshot, client, source_loader=source_loader, expected_period=issue_date)
    analyzer_path = output_dir / "analyzer.json"
    analyzer_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = organize_report(analysis)
    report_path, html_path = write_report(report, output_dir)
    return PipelineResult(report_path, html_path, collector_path, analyzer_path)
