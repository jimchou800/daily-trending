"""Scheduled hosted runner; local output is a disposable preview only."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .dashscope import BudgetExceeded, DashScopeClient
from .hosted import RemoteArtifactStore, RemoteAuthority, RemoteModelTransport
from .pipeline import run_live_pipeline
from .scheduler import BEIJING, MAX_ATTEMPTS, publication_deadline


def run_hosted_daily(
    authority: RemoteAuthority,
    output_dir: Path,
    *,
    clock: Callable[[], datetime] | None = None,
    client: DashScopeClient | None = None,
    fetch: Callable | None = None,
    source_loader: Callable | None = None,
    run_id: str | None = None,
) -> dict:
    """Generate and commit today's issue through the remote authoritative ledger.

    Authority fencing, attempts, snapshot/cache, history and money survive an
    empty runner directory. No filesystem publication constitutes hosted success.
    Server checks remain authoritative if the caller clock or scheduler drifts.
    """
    clock = clock or (lambda: datetime.now(BEIJING))
    current = clock()
    if current.tzinfo is None:
        raise ValueError("hosted clock must be timezone-aware")
    period = current.astimezone(BEIJING).date().isoformat()

    def check_deadline() -> None:
        now = clock()
        if (
            now.tzinfo is None
            or now.astimezone(BEIJING).date().isoformat() != period
            or now >= publication_deadline(period)
        ):
            raise TimeoutError("hosted publication deadline has passed")

    check_deadline()
    client = client or DashScopeClient()
    original_guard, original_transport = client.before_call, client.transport
    initial = authority.state(period)
    if initial.get("publication") is not None:
        return initial["publication"]
    identity = f"{run_id or 'runner'}:{uuid4().hex}"
    last_error: Exception | None = None
    try:
        for attempt in range(MAX_ATTEMPTS):
            check_deadline()
            # begin is server-persisted and rejects a fourth distinct attempt,
            # even after a runner restart or overlapping scheduler delivery.
            token = authority.call("begin", {"period": period, "run_id": f"{identity}:{attempt}"})
            fence = token["fence"]
            store = RemoteArtifactStore(authority, period, fence)
            if store.remote.get("publication") is not None:
                return store.remote["publication"]
            client.transport = RemoteModelTransport(authority, period, fence, original_transport)

            def guard() -> None:
                check_deadline()
                if original_guard:
                    original_guard()

            client.before_call = guard
            try:
                result = run_live_pipeline(
                    output_dir,
                    period,
                    clock().astimezone(timezone.utc).isoformat(timespec="seconds"),
                    client,
                    fetch=fetch,
                    source_loader=source_loader,
                    artifact_store=store,
                    history=store.remote["history"],
                )
                check_deadline()
                report = json.loads(result.report_path.read_text(encoding="utf-8"))
                staged = authority.call("stage", {"period": period, "fence": fence, "report": report})
                check_deadline()
                publication = authority.call("publish", {"period": period, "fence": fence, "hash": staged["hash"]})
                return publication["report"]
            except BudgetExceeded:
                raise
            except Exception as exc:
                last_error = exc
                # Lost publication acknowledgement can be recovered even after
                # cutoff, without replaying generation or mutating the archive.
                recovered = authority.state(period)
                if recovered.get("publication") is not None:
                    return recovered["publication"]
        raise RuntimeError("hosted generation attempts exhausted") from last_error
    finally:
        client.before_call, client.transport = original_guard, original_transport


def main() -> int:
    """Run from secret environment bindings without logging credentials or payloads."""
    try:
        authority = RemoteAuthority(
            os.environ["PUBLICATION_BASE_URL"],
            os.environ["PUBLICATION_AUTOMATION_TOKEN"],
            os.environ["PUBLICATION_INSTALLATION_ID"],
        )
        with tempfile.TemporaryDirectory(prefix="daily-trending-") as directory:
            report = run_hosted_daily(authority, Path(directory), run_id=os.environ.get("GITHUB_RUN_ID"))
        print(f"Published /archive/{report['report_date']}/")
        return 0
    except Exception:
        print("Hosted daily publication failed; inspect private authority state.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
