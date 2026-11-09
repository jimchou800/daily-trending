"""Analyzer stage that joins saved model responses to collected entries."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from .contracts import CONTRACT_VERSION, require, require_github_url, validate_common_artifact
from .dashscope import DashScopeClient, build_analysis_prompt
from .github_sources import load_github_sources
from .persistence import ArtifactStore


def analyze_snapshot(snapshot: dict, response_path: Path, expected_period: str | None = None) -> dict:
    """Validate a collector artifact and produce ordered AI analyses.

    Heat metrics and rank are copied exclusively from the collector artifact;
    response values for those fields are ignored.  Non-AI entries are filtered
    in source order and are never replaced with outside projects.
    """
    validate_common_artifact(snapshot, "collector")
    if expected_period is not None and snapshot["period_id"] != expected_period:
        raise ValueError("period does not match requested run")
    source = snapshot["snapshot"]
    require(source.get("scope"), "missing snapshot scope")
    entries = source.get("entries")
    require(isinstance(entries, list), "snapshot entries are missing")
    try:
        payload = json.loads(Path(response_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("model responses could not be read") from exc
    if payload.get("snapshot_id") is not None and payload.get("snapshot_id") != snapshot["snapshot_id"]:
        raise ValueError("model response snapshot does not match collector snapshot")
    responses = payload.get("analyses")
    require(isinstance(responses, list), "model analyses are missing")
    by_repository = {item.get("repository"): item for item in responses if isinstance(item, dict)}
    analyses = []
    errors = []
    for entry in entries:
        response = by_repository.get(entry["repository"])
        if response is None:
            errors.append(f"missing analysis for {entry['repository']}")
            continue
        if not isinstance(response.get("is_ai"), bool):
            errors.append(f"invalid relevance for {entry['repository']}")
            continue
        if not response["is_ai"]:
            continue
        required = ("ai_relevance_evidence", "project_type", "introduction", "problem_solved", "audience", "characteristics")
        if any(not isinstance(response.get(field), str) or not response[field].strip() for field in required):
            errors.append(f"incomplete analysis for {entry['repository']}")
            continue
        sources = response.get("sources")
        if not isinstance(sources, list) or not sources or any(not isinstance(url, str) for url in sources):
            errors.append(f"missing sources for {entry['repository']}")
            continue
        try:
            for url in sources:
                require_github_url(url, "source")
        except ValueError:
            errors.append(f"non-GitHub source for {entry['repository']}")
            continue
        reason_has_evidence = response.get("reason_has_evidence")
        if not isinstance(reason_has_evidence, bool):
            errors.append(f"invalid reason evidence for {entry['repository']}")
            continue
        reason = response.get("reason")
        if reason_has_evidence and (not isinstance(reason, str) or not reason.strip()):
            errors.append(f"missing reason for {entry['repository']}")
            continue
        analyses.append(
            {
                "repository": entry["repository"],
                "repository_url": entry["repository_url"],
                "rank": entry["rank"],
                "ai_relevance_evidence": response["ai_relevance_evidence"],
                "project_type": response["project_type"],
                "introduction": response["introduction"],
                "problem_solved": response["problem_solved"],
                "audience": response["audience"],
                "characteristics": response["characteristics"],
                "heat": {"stars_today": entry.get("stars_today"), "stars_total": entry.get("stars_total")},
                "reason_has_evidence": reason_has_evidence,
                "reason": reason if reason_has_evidence else None,
                "sources": sources,
            }
        )
    if not analyses and any(item.get("is_ai") is True for item in responses if isinstance(item, dict)) and errors:
        status = "failed"
    else:
        status = "succeeded"
    return {
        "contract_version": CONTRACT_VERSION,
        "period_id": snapshot["period_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "stage": {"name": "analyzer", "status": status, "errors": errors},
        "snapshot": {"source_url": source["source_url"], "scope": source["scope"], "collected_at": source["collected_at"]},
        "analyses": analyses,
    }


def analyze_live_snapshot(
    snapshot: dict,
    client: DashScopeClient,
    source_loader: Callable[[dict], dict[str, Any]] | None = None,
    *,
    expected_period: str | None = None,
    cache_store: ArtifactStore | None = None,
) -> dict:
    """Analyze each collected repository through DashScope in source order.

    A candidate failure is recorded and does not prevent later candidates from
    being attempted. If no candidate produces a reliable analysis, the stage
    remains failed rather than becoming a false zero-project result.
    """
    validate_common_artifact(snapshot, "collector")
    if expected_period is not None and snapshot["period_id"] != expected_period:
        raise ValueError("period does not match requested run")
    entries = snapshot["snapshot"].get("entries")
    require(isinstance(entries, list), "snapshot entries are missing")
    analyses: list[dict] = []
    errors: list[str] = []
    completed = False
    for entry in entries:
        try:
            if cache_store:
                cached = cache_store.load_analysis(entry["repository"], snapshot["snapshot_id"], client.config.model)
                if cached is not None:
                    analyses.append(_validated_entry(snapshot, entry, cached))
                    completed = True
                    continue
            source = source_loader(entry) if source_loader else load_github_sources(entry)
            prompt_entry = dict(entry)
            prompt_entry["github_sources"] = source
            raw = client.complete(build_analysis_prompt([prompt_entry]))
            decoded = json.loads(raw)
            responses = decoded.get("analyses") if isinstance(decoded, dict) and isinstance(decoded.get("analyses"), list) else [decoded]
            if not isinstance(decoded, dict) or not isinstance(responses, list):
                raise ValueError("model analyses are not an object")
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as response_file:
                json.dump({"snapshot_id": snapshot["snapshot_id"], "analyses": responses}, response_file)
                response_path = Path(response_file.name)
            try:
                result = analyze_snapshot(snapshot, response_path, expected_period=expected_period)
            finally:
                response_path.unlink(missing_ok=True)
            completed = True
            analyses.extend(result["analyses"])
            if cache_store and result["analyses"]:
                cached_response = next((item for item in responses if isinstance(item, dict) and item.get("repository") == entry["repository"]), None)
                if cached_response is not None:
                    cache_store.save_analysis(entry["repository"], snapshot["snapshot_id"], client.config.model, cached_response)
            errors.extend(result["stage"]["errors"])
        except Exception as exc:  # isolated candidate failure by contract
            errors.append(f"analysis failed for {entry['repository']}: {exc}")
    status = "succeeded" if completed else "failed"
    source = snapshot["snapshot"]
    return {
        "contract_version": CONTRACT_VERSION,
        "period_id": snapshot["period_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "stage": {"name": "analyzer", "status": status, "errors": errors},
        "snapshot": {"source_url": source["source_url"], "scope": source["scope"], "collected_at": source["collected_at"]},
        "analyses": analyses,
    }


def _validated_entry(snapshot: dict, entry: dict, response: dict) -> dict:
    """Validate a cached response through the same analyzer contract as fresh output."""
    from tempfile import NamedTemporaryFile
    with NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump({"snapshot_id": snapshot["snapshot_id"], "analyses": [response]}, handle)
        path = Path(handle.name)
    try:
        result = analyze_snapshot(snapshot, path, expected_period=snapshot["period_id"])
    finally:
        path.unlink(missing_ok=True)
    if not result["analyses"]:
        raise ValueError("cached analysis is incompatible")
    return result["analyses"][0]
