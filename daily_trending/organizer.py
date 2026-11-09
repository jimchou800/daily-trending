"""Organizer stage for report JSON and responsive static HTML."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .contracts import CONTRACT_VERSION, MISSING_METRIC, TRENDING_SCOPE, UNKNOWN_REASON, require, require_github_url, require_timestamp, validate_common_artifact


def organize_report(analysis: dict, published_at: str | None = None) -> dict:
    """Validate an analyzer artifact and create canonical report JSON data."""
    validate_common_artifact(analysis, "analyzer")
    if published_at is not None:
        require_timestamp(published_at, "published_at")
    entries = analysis.get("analyses")
    require(isinstance(entries, list) and len(entries) <= 5, "invalid report entry count")
    report_entries = []
    for item in entries:
        required = ("repository", "repository_url", "rank", "ai_relevance_evidence", "project_type", "introduction", "problem_solved", "audience", "characteristics", "heat", "sources")
        require(all(field in item for field in required), "analysis entry is incomplete")
        require_github_url(item["repository_url"], "repository")
        require(isinstance(item["sources"], list) and item["sources"], "analysis sources are missing")
        heat = item["heat"]
        require(heat.get("stars_today") is None or isinstance(heat.get("stars_today"), int), "invalid stars_today")
        require(heat.get("stars_total") is None or isinstance(heat.get("stars_total"), int), "invalid stars_total")
        report_entries.append(dict(item))
    return {
        "contract_version": CONTRACT_VERSION,
        "report_date": analysis["period_id"],
        "timezone": "Asia/Shanghai",
        "collected_at": analysis["snapshot"]["collected_at"],
        "published_at": published_at,
        "source": analysis["snapshot"]["source_url"],
        "scope": TRENDING_SCOPE,
        "status": "published" if published_at else "sample_preview",
        "entry_count": len(report_entries),
        "entries": report_entries,
    }


def render_html(report: dict) -> str:
    """Render a canonical report into a self-contained mobile-friendly page."""
    cards = []
    for item in report["entries"]:
        heat = item["heat"]
        stars_today = MISSING_METRIC if heat.get("stars_today") is None else f"{heat['stars_today']}（当日新增）"
        stars_total = MISSING_METRIC if heat.get("stars_total") is None else f"{heat['stars_total']}（累计）"
        reason = item.get("reason") if item.get("reason_has_evidence") else UNKNOWN_REASON
        cards.append(
            "<article class=\"entry\">"
            f"<h2>{html.escape(item['repository'])}</h2>"
            f"<p class=\"type\">{html.escape(item['project_type'])} · 榜单第 {item['rank']} 位</p>"
            f"<p>{html.escape(item['introduction'])}</p>"
            f"<dl><dt>解决的问题</dt><dd>{html.escape(item['problem_solved'])}</dd>"
            f"<dt>适用读者</dt><dd>{html.escape(item['audience'])}</dd>"
            f"<dt>项目特点</dt><dd>{html.escape(item['characteristics'])}</dd>"
            f"<dt>热度</dt><dd>当日新增 Star：{html.escape(str(stars_today))}；累计 Star：{html.escape(str(stars_total))}</dd>"
            f"<dt>走红原因</dt><dd>{html.escape(reason)}</dd></dl>"
            + (f"<p>{html.escape(item['repeat_change'])} · <a href=\"{html.escape(item['previous_report_url'], quote=True)}\">上次介绍</a></p>" if item.get("repeat_change") and item.get("previous_report_url") else "")
            + f"<p><a href=\"{html.escape(item['repository_url'], quote=True)}\">查看仓库</a> · "
            + " · ".join(f'<a href=\"{html.escape(url, quote=True)}\">来源</a>' for url in item["sources"])
            + "</p></article>"
        )
    if not cards:
        cards.append('<p class="empty">今日无符合收录条件的项目。</p>')
    published = report["published_at"] or "未发布（样本预览）"
    return "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>AI 开源项目热点日报</title><style>" \
        "*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#17202a;font-family:system-ui,-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif;line-height:1.65}main{max-width:900px;margin:0 auto;padding:24px 16px 48px}header{background:#102a43;color:#fff;padding:24px 20px;border-radius:10px;margin-bottom:20px}h1{font-size:clamp(1.5rem,4vw,2.2rem);margin:0 0 8px}h2{margin:0;font-size:1.25rem}.meta{font-size:.95rem;color:#d9e2ec}.entry{background:#fff;border:1px solid #d9e2ec;border-radius:8px;padding:20px;margin:16px 0}.type{color:#486581;font-weight:600}dl{display:grid;grid-template-columns:minmax(100px,145px) 1fr;gap:5px 16px}dt{font-weight:700}dd{margin:0}a{color:#0b69a3} @media(max-width:600px){main{padding:12px 10px 32px}header,.entry{padding:16px}dl{display:block}dt{margin-top:10px}dd{margin-bottom:4px}}</style></head><body><main><header>" \
        f"<h1>AI 开源项目热点日报</h1><div class=\"meta\">日报日期：{html.escape(report['report_date'])} · 实际发布时间：{html.escape(published)}</div><div class=\"meta\">采集时间：{html.escape(report['collected_at'])} · 来源：GitHub Trending Daily（全部编程语言） · 项目数：{report['entry_count']}</div></header>" \
        + "".join(cards) + "</main></body></html>"


def write_report(report: dict, output_dir: Path) -> tuple[Path, Path]:
    """Write canonical report JSON and its HTML rendering atomically enough for sample use."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.json"
    html_path = output_dir / "index.html"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    return report_path, html_path
