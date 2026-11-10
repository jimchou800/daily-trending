"""Analyzer stage that joins saved model responses to collected entries."""

from __future__ import annotations

from .contracts import SelectionOutcome, SelectionReason, Stage, Status

import json
from pathlib import Path
from typing import Any, Callable

from .contracts import CONTRACT_VERSION, require, require_github_url, validate_common_artifact
from .dashscope import DashScopeClient, BudgetExceeded
from .analysis_prompts import relevance_context, relevance_batches, detail_prompt
from .selection import filter_repeat_entries
from .github_sources import load_github_sources
from .persistence import ArtifactStore


def analyze_snapshot(snapshot: dict, response_path: Path, expected_period: str | None = None) -> dict:
    """Validate a collector artifact and produce ordered AI analyses.

    Heat metrics and rank are copied exclusively from the collector artifact;
    response values for those fields are ignored.  Non-AI entries are filtered
    in source order and are never replaced with outside projects.
    """
    validate_common_artifact(snapshot, Stage.COLLECTOR)
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
        required = (
            "ai_relevance_evidence",
            "project_type",
            "introduction",
            "problem_solved",
            "audience",
            "characteristics",
        )
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
        status = Status.FAILED
    else:
        status = Status.SUCCEEDED
    return {
        "contract_version": CONTRACT_VERSION,
        "period_id": snapshot["period_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "stage": {"name": Stage.ANALYZER, "status": status, "errors": errors},
        "snapshot": {
            "source_url": source["source_url"],
            "scope": source["scope"],
            "collected_at": source["collected_at"],
        },
        "analyses": analyses,
    }


def analyze_live_snapshot(
    snapshot: dict,
    client: DashScopeClient,
    source_loader: Callable[[dict], dict[str, Any]] | None = None,
    *,
    expected_period: str | None = None,
    cache_store: ArtifactStore | None = None,
    history: list[dict] | None = None,
    releases_by_repository: dict[str, list[dict]] | None = None,
) -> dict:
    """Classify all candidates, apply repeat eligibility, then analyze up to five successes.

    Sources and validated model output are cached by snapshot/model/schema. Missing
    classifications are incomplete work, never a verified empty selection.
    """
    validate_common_artifact(snapshot, Stage.COLLECTOR)
    if expected_period is not None and snapshot["period_id"] != expected_period:
        raise ValueError("period does not match requested run")
    entries = snapshot["snapshot"].get("entries")
    require(isinstance(entries, list), "snapshot entries are missing")
    analyses, errors, decisions = [], [], []
    release_evidence, sources, classifications = {}, {}, {}
    model, sid = client.config.model, snapshot["snapshot_id"]
    pending = []
    failed_details = set()
    for entry in entries:
        repo = entry["repository"]
        try:
            source = cache_store.load_analysis("source:" + repo, sid, model) if cache_store else None
            if source is None:
                source = source_loader(entry) if source_loader else load_github_sources(entry)
                require(isinstance(source, dict), "invalid GitHub sources")
                source = dict(source, repository="source:" + repo)
                if cache_store:
                    cache_store.save_analysis("source:" + repo, sid, model, source)
            sources[repo] = source
            release_evidence[repo] = [
                r
                for r in source.get("releases", [])
                if isinstance(r, dict) and r.get("draft") is False and r.get("prerelease") is False
            ]
            context = relevance_context(entry, source)
            cached = cache_store.load_analysis("relevance:" + repo, sid, model) if cache_store else None
            if cached is not None:
                cached = dict(cached, repository=repo)
                _validate_relevance(cached, context)
                classifications[repo] = cached
            else:
                pending.append(context)
        except Exception as exc:
            errors.append(f"source/classification failed for {repo}: {exc}")
    for batch, prompt in relevance_batches(pending):
        try:
            decoded = json.loads(client.complete(prompt))
            responses = decoded.get("classifications")
            require(isinstance(responses, list), "missing classifications")
            expected = {c["repository"] for c in batch}
            require(len(responses) == len(expected), "incomplete classifications")
            require(all(isinstance(r, dict) for r in responses), "invalid classifications")
            require({r.get("repository") for r in responses} == expected, "classification repositories mismatch")
            by_repo = {r["repository"]: r for r in responses}
            for context in batch:
                response = by_repo[context["repository"]]
                _validate_relevance(response, context)
                classifications[context["repository"]] = response
                if cache_store:
                    cache_store.save_analysis(
                        "relevance:" + context["repository"],
                        sid,
                        model,
                        dict(response, repository="relevance:" + context["repository"]),
                    )
        except Exception as exc:
            errors.append(f"classification failed: {exc}")
    complete = len(classifications) == len(entries) and not errors
    eligible = []
    if complete:
        candidates = []
        for entry in entries:
            relevance = classifications[entry["repository"]]
            if relevance["is_ai"] is True:
                candidates.append(entry)
            else:
                decisions.append(
                    {
                        "repository": entry["repository"],
                        "eligible": False,
                        "reason": (
                            SelectionReason.NOT_AI if relevance["is_ai"] is False else SelectionReason.UNKNOWN_RELEVANCE
                        ),
                        "evidence": relevance["ai_relevance_evidence"],
                    }
                )
        eligible, repeat_decisions = filter_repeat_entries(
            candidates,
            snapshot["period_id"],
            history or [],
            releases_by_repository=releases_by_repository if releases_by_repository is not None else release_evidence,
            limit=len(candidates),
        )
        decisions.extend(repeat_decisions)
        for entry in eligible:
            if len(analyses) >= 5:
                break
            repo = entry["repository"]
            try:
                response = cache_store.load_analysis(repo, sid, model) if cache_store else None
                if response is None:
                    decoded = json.loads(client.complete(detail_prompt(entry, sources[repo])))
                    responses = decoded.get("analyses", [decoded])
                    require(isinstance(responses, list) and len(responses) == 1, "invalid detail response")
                    response = responses[0]
                require(isinstance(response, dict) and response.get("repository") == repo, "detail repository mismatch")
                require(response.get("is_ai") is True, "detail did not confirm AI")
                response = dict(response, ai_relevance_evidence=classifications[repo]["ai_relevance_evidence"])
                analysis = _validated_entry(snapshot, entry, response)
                allowed = {
                    entry["repository_url"],
                    entry["repository_url"] + "#readme",
                    entry["repository_url"] + "/blob/HEAD/README.md",
                }
                allowed.update(r.get("html_url") for r in release_evidence[repo])
                require(all(url in allowed for url in analysis["sources"]), "detail cites unprovided source")
                for key in ("repeat_basis", "repeat_change", "previous_report_url"):
                    if key in entry:
                        analysis[key] = entry[key]
                analysis["sources"] = list(dict.fromkeys(analysis["sources"] + entry.get("sources", [])))
                analyses.append(analysis)
                if cache_store:
                    cache_store.save_analysis(repo, sid, model, response)
            except (BudgetExceeded, TimeoutError) as exc:
                errors.append(str(exc))
                complete = False
                break
            except Exception as exc:
                failed_details.add(repo)
                errors.append(f"analysis failed for {repo}: {exc}")
    status = Status.SUCCEEDED if complete and (analyses or not eligible) else Status.FAILED
    by_decision = {decision["repository"]: decision for decision in decisions}
    selected_repos = {analysis["repository"] for analysis in analyses}
    proof_decisions = []
    for entry in entries:
        repo = entry["repository"]
        relevance = classifications.get(repo, {})
        decision = dict(
            by_decision.get(
                repo, {"repository": repo, "eligible": False, "reason": SelectionReason.CLASSIFICATION_INCOMPLETE}
            )
        )
        decision.update(
            is_ai=relevance.get("is_ai"),
            ai_relevance_evidence=relevance.get("ai_relevance_evidence", ""),
            releases=release_evidence.get(repo, []),
        )
        decision["outcome"] = (
            SelectionOutcome.SELECTED
            if repo in selected_repos
            else SelectionOutcome.ANALYSIS_FAILED if repo in failed_details else SelectionOutcome.EXCLUDED
        )
        if decision.get("eligible") and decision["outcome"] == SelectionOutcome.EXCLUDED:
            decision.update(eligible=False, reason=SelectionReason.LIMIT_REACHED)
        proof_decisions.append(decision)
    selection_proof = {
        "completed": complete,
        "eligible_count": len(analyses) + len(failed_details),
        "decisions": proof_decisions,
    }
    source = snapshot["snapshot"]
    return {
        "contract_version": CONTRACT_VERSION,
        "period_id": snapshot["period_id"],
        "snapshot_id": sid,
        "analysis_schema": "2",
        "stage": {"name": Stage.ANALYZER, "status": status, "errors": errors},
        "snapshot": {key: source[key] for key in ("source_url", "scope", "collected_at")},
        "analyses": analyses,
        "release_evidence": release_evidence,
        Stage.SELECTION: sorted(
            decisions, key=lambda d: next(i for i, e in enumerate(entries) if e["repository"] == d["repository"])
        ),
        "classifications": list(classifications.values()),
        "selection_proof": selection_proof,
    }


def _validate_relevance(response: dict, context: dict) -> None:
    verdict = response.get("is_ai")
    require(verdict is None or type(verdict) is bool, "invalid relevance verdict")
    require("is_ai" in response, "missing relevance verdict")
    evidence = response.get("ai_relevance_evidence")
    require(isinstance(evidence, str), "missing relevance evidence")
    if verdict is not None:
        require(
            bool(evidence.strip()) and any(evidence in context[key] for key in ("description", "readme")),
            "relevance evidence is not a provided source quote",
        )


def _validated_entry(snapshot: dict, entry: dict, response: dict) -> dict:
    """Validate a cached response through the same analyzer contract as fresh output."""
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump({"snapshot_id": snapshot["snapshot_id"], "analyses": [response]}, handle)
        path = Path(handle.name)
    try:
        candidate = dict(snapshot, snapshot=dict(snapshot["snapshot"], entries=[entry]))
        result = analyze_snapshot(candidate, path, expected_period=snapshot["period_id"])
    finally:
        path.unlink(missing_ok=True)
    if not result["analyses"]:
        raise ValueError("cached analysis is incompatible")
    return result["analyses"][0]
