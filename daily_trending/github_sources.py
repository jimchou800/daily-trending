"""Read only GitHub repository sources allowed by the product specification."""

from __future__ import annotations

import base64
import json
from typing import Any, Callable
from urllib.request import Request, urlopen


def load_github_sources(entry: dict, fetch: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """Load repository metadata, README, and formal releases from GitHub only."""
    repository = entry.get("repository")
    if not isinstance(repository, str) or repository.count("/") != 1:
        raise ValueError("invalid repository identifier")
    fetcher = fetch or _get_json
    base = f"https://api.github.com/repos/{repository}"
    metadata = fetcher(base)
    readme = fetcher(f"{base}/readme")
    releases = fetcher(f"{base}/releases?per_page=5")
    content = readme.get("content", "") if isinstance(readme, dict) else ""
    if content:
        content = base64.b64decode(content).decode("utf-8", errors="replace")
    formal_releases = []
    if isinstance(releases, list):
        formal_releases = [
            {
                "tag_name": item.get("tag_name"),
                "published_at": item.get("published_at"),
                "html_url": item.get("html_url"),
            }
            for item in releases
            if isinstance(item, dict) and item.get("draft") is False and item.get("prerelease") is False
        ]
    return {"metadata": metadata, "readme": content, "releases": formal_releases}


def _get_json(url: str) -> Any:
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "daily-trending/1.0"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))
