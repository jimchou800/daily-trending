"""Collector stage for validated, saved GitHub Trending snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

from .contracts import (
    CONTRACT_VERSION,
    TRENDING_SCOPE,
    TRENDING_URL,
    require,
    require_date,
    require_github_url,
    require_timestamp,
)


class _TrendingParser(HTMLParser):
    """Extract repository cards without depending on a third-party scraper."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict] = []
        self._card: dict | None = None
        self._depth = 0
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._link: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "article" and "Box-row" in (attributes.get("class") or "").split():
            self._card = {"href": None, "repository": None, "description": "", "language": None, "stars_total": None, "stars_today": None, "text": [], "total_text": "", "today_text": ""}
            self._depth = 1
            return
        if self._card is None:
            return
        self._depth += 1
        self._link = attributes.get("href") if tag == "a" else self._link
        if tag == "p":
            self._capture = "description"
            self._buffer = []
        elif tag == "span" and attributes.get("itemprop") == "programmingLanguage":
            self._capture = "language"
            self._buffer = []
        elif tag == "span" and "float-sm-right" in (attributes.get("class") or "").split():
            self._capture = "today"
            self._buffer = []
        elif tag == "a" and (attributes.get("href") or "").startswith("/"):
            self._capture = "total" if (attributes.get("href") or "").endswith("/stargazers") else "link"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._card is None:
            return
        clean = " ".join(data.split())
        if clean:
            self._card["text"].append(clean)
            if self._capture:
                self._buffer.append(clean)

    def handle_endtag(self, tag: str) -> None:
        if self._card is None:
            return
        if self._capture and ((tag == "p" and self._capture == "description") or (tag == "span" and self._capture in ("language", "today")) or (tag == "a" and self._capture in ("link", "total"))):
            value = " ".join(self._buffer).strip()
            if self._capture == "description":
                self._card["description"] = value
            elif self._capture == "language":
                self._card["language"] = value or None
            elif self._capture == "today":
                self._card["today_text"] = value
            elif self._capture == "total":
                self._card["total_text"] = value
            elif self._capture == "link" and self._link and self._link.count("/") == 2 and not self._card["repository"]:
                self._card["href"] = self._link
                self._card["repository"] = value.replace(" ", "")
            self._capture = None
            self._buffer = []
        if tag == "article":
            text = " ".join(self._card["text"])
            if self._card["href"]:
                total_match = re.search(r"([\d,]+)", self._card["total_text"])
                today_match = re.search(r"([\d,]+)\s+stars?\s+today", self._card["today_text"], re.IGNORECASE)
                self._card["stars_total"] = int(total_match.group(1).replace(",", "")) if total_match else None
                self._card["stars_today"] = int(today_match.group(1).replace(",", "")) if today_match else None
                self.cards.append(self._card)
            self._card = None
            self._capture = None
            self._depth = 0
        else:
            self._depth -= 1


def parse_trending_html(html: str) -> list[dict]:
    """Parse every GitHub Trending Daily repository card in source order.

    A page with no repository cards is rejected because it may be an error or
    sign-in page rather than a complete snapshot.
    """
    parser = _TrendingParser()
    parser.feed(html)
    require(parser.cards, "trending response is not complete")
    entries = []
    for rank, card in enumerate(parser.cards, start=1):
        repository = card["repository"]
        require(isinstance(repository, str) and repository.count("/") == 1, "trending repository is invalid")
        entries.append(
            {
                "rank": rank,
                "repository": repository,
                "repository_url": f"https://github.com/{repository}",
                "description": card["description"],
                "language": card["language"],
                "stars_total": card["stars_total"],
                "stars_today": card["stars_today"],
            }
        )
    return entries


def collect_trending(issue_date: str, collected_at: str, fetch: Callable[[str], str] | None = None) -> dict:
    """Fetch and validate the complete GitHub Daily all-language snapshot."""
    require_date(issue_date, "period")
    require_timestamp(collected_at, "collected_at")
    if fetch is None:
        def fetch(url: str) -> str:
            request = Request(url, headers={"User-Agent": "daily-trending/1.0"})
            with urlopen(request, timeout=20) as response:
                return response.read().decode("utf-8")
    entries = parse_trending_html(fetch(TRENDING_URL))
    return _artifact_from_entries(entries, issue_date, collected_at)


def _artifact_from_entries(entries: list[dict], issue_date: str, collected_at: str) -> dict:
    normalized = []
    for index, entry in enumerate(entries, start=1):
        require(entry.get("rank") == index, "snapshot ranks must preserve source order")
        require_github_url(entry.get("repository_url"), "repository")
        require(isinstance(entry.get("repository"), str) and entry["repository"], "snapshot repository is missing")
        for metric in ("stars_today", "stars_total"):
            require(entry.get(metric) is None or isinstance(entry.get(metric), int), f"invalid {metric}")
        normalized.append(dict(entry))
    require(normalized, "snapshot entries are missing")
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "contract_version": CONTRACT_VERSION,
        "period_id": issue_date,
        "snapshot_id": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
        "stage": {"name": "collector", "status": "succeeded", "errors": []},
        "snapshot": {"source_url": TRENDING_URL, "scope": TRENDING_SCOPE, "collected_at": collected_at, "entries": normalized},
    }


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
    return _artifact_from_entries(normalized, issue_date, collected_at)
