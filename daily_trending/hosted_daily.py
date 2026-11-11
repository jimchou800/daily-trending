"""Scheduled hosted runner; local output is a disposable preview only."""

from __future__ import annotations

import json
import math
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
from .scheduler import BEIJING, MAX_ATTEMPTS


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

    def check_period() -> None:
        now = clock()
        if (
            now.tzinfo is None
            or now.astimezone(BEIJING).date().isoformat() != period
        ):
            raise ValueError("hosted publication must target the current Beijing date")

    check_period()
    client = client or DashScopeClient()
    original_guard, original_transport = client.before_call, client.transport

    def recover_transport(operation: Callable):
        # Reads and idempotent admission may be replayed, never a new run identity
        # or a paid model dispatch. Bound recovery even when the server is down.
        for recovery in range(MAX_ATTEMPTS):
            check_period()
            try:
                return operation()
            except OSError:
                if recovery == MAX_ATTEMPTS - 1:
                    raise

    initial = recover_transport(lambda: authority.state(period))
    if initial.get("publication") is not None:
        return initial["publication"]
    identity = f"{run_id or 'runner'}:{uuid4().hex}"
    last_error: Exception | None = None
    try:
        for attempt in range(MAX_ATTEMPTS):
            check_period()
            # begin is server-persisted and rejects a fourth distinct attempt,
            # even after a runner restart or overlapping scheduler delivery.
            token = recover_transport(
                lambda: authority.call("begin", {"period": period, "run_id": f"{identity}:{attempt}"})
            )
            fence = token["fence"]
            store = recover_transport(lambda: RemoteArtifactStore(authority, period, fence))
            if store.remote.get("publication") is not None:
                return store.remote["publication"]
            client.transport = RemoteModelTransport(authority, period, fence, original_transport)

            def guard() -> None:
                check_period()
                if original_guard:
                    original_guard()
                # The local monetary guard runs before the remote transport.
                # Notify only for that exact guard, never for accounting or
                # per-period call limits. The authority rechecks its durable
                # month total; this notice grants no dispatch and charges zero.
                if (
                    not client.accounting_blocked
                    and client.calls_used < client.config.max_calls
                    and client.estimated_spend_cny + client.pending_call_cost_cny > client.config.monthly_budget_cny
                ):
                    authority.call(
                        "budget-notice",
                        {
                            "period": period,
                            "fence": fence,
                            "input_tokens": client.pending_input_tokens,
                            "output_tokens": client.pending_output_tokens,
                            "limit_micros": math.floor(client.config.monthly_budget_cny * 1_000_000),
                        },
                    )

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
                check_period()
                report = json.loads(result.report_path.read_text(encoding="utf-8"))
                staged = authority.call("stage", {"period": period, "fence": fence, "report": report})
                check_period()
                publication = authority.call("publish", {"period": period, "fence": fence, "hash": staged["hash"]})
                return publication["report"]
            except BudgetExceeded:
                raise
            except Exception as exc:
                last_error = exc
                # Lost publication acknowledgement can be recovered even after
                # cutoff, without replaying generation or mutating the archive.
                recovered = recover_transport(lambda: authority.state(period))
                if recovered.get("publication") is not None:
                    return recovered["publication"]
        raise RuntimeError("hosted generation attempts exhausted") from last_error
    finally:
        client.before_call, client.transport = original_guard, original_transport


def main() -> int:
    """读取密钥环境变量运行托管任务，并输出不含私密载荷的失败诊断。"""
    authority: RemoteAuthority | None = None
    try:
        base_url = os.environ["PUBLICATION_BASE_URL"]
        token = os.environ["PUBLICATION_AUTOMATION_TOKEN"]
        installation = os.environ["PUBLICATION_INSTALLATION_ID"]
        authority = RemoteAuthority(
            base_url,
            token,
            installation,
        )
        with tempfile.TemporaryDirectory(prefix="daily-trending-") as directory:
            report = run_hosted_daily(authority, Path(directory), run_id=os.environ.get("GITHUB_RUN_ID"))
        print(f"已发布 /archive/{report['report_date']}/")
        return 0
    except Exception as exc:
        print("托管日报发布失败；请检查私有权威状态。", file=sys.stderr)
        failure_type = "配置" if isinstance(exc, KeyError) else type(exc).__name__
        print(f"失败类型：{failure_type}", file=sys.stderr)
        if authority is not None:
            period = datetime.now(BEIJING).date().isoformat()
            try:
                state = authority.state(period)
                month = state.get("month") if isinstance(state.get("month"), dict) else {}
                print(
                    "权威摘要："
                    f"period={period} attempts={state.get('attempts', '?')} "
                    f"fence={state.get('fence', '?')} "
                    f"publication={'present' if state.get('publication') is not None else 'absent'} "
                    f"reservations={len(state.get('reservations', {})) if isinstance(state.get('reservations'), dict) else '?'} "
                    f"accounting_blocked={month.get('accounting_blocked', '?')} "
                    f"generation_exhausted={month.get('generation_exhausted', '?')}",
                    file=sys.stderr,
                )
            except Exception as state_exc:
                print(f"权威摘要不可用：{type(state_exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
