#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import Any, Callable


PROGRESS_EDGE_TYPE = "PHASE2_EVIDENCE_CYCLE_PROGRESS_V1"
TIMER_UNIT = "pio-phase2-isolated-evidence-cycle.timer"

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]
Now = Callable[[], datetime]


@dataclass(frozen=True)
class RateLimitCycle:
    evidence_id: int
    pool_address: str
    as_of: str
    rpc_rate_limited: bool


@dataclass(frozen=True)
class Phase2RateLimitAutopauseReport:
    database_ready: bool
    database_path: str
    pool_address: str | None
    cycles_checked: int
    consecutive_rpc_rate_limited: int
    rate_limit_streak_threshold: int
    repeated_provider_rejection: bool
    latest_cycle_age_seconds: float | None
    latest_cycle_recent: bool
    timer_enabled_before: bool
    timer_active_before: bool
    pause_recommended: bool
    apply_requested: bool
    applied: bool
    timer_enabled_after: bool
    timer_active_after: bool
    future_timer_cycles_paused: bool
    failure_step: str | None
    read_only_evidence_check: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _payload_rate_limited(payload: dict[str, Any]) -> bool:
    direct = payload.get("rpc_rate_limited")
    if isinstance(direct, bool):
        return direct

    outcomes = payload.get("stage_outcomes")
    if not isinstance(outcomes, list):
        return False
    return any(
        isinstance(item, list)
        and len(item) == 3
        and item[2] == "RPC_RATE_LIMITED"
        for item in outcomes
    )


def _read_cycles(
    database_path: Path,
    *,
    limit: int,
) -> tuple[RateLimitCycle, ...]:
    if (
        limit <= 0
        or database_path.is_symlink()
        or not database_path.is_file()
    ):
        return ()

    uri = f"file:{database_path.resolve()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            latest = conn.execute(
                """
                SELECT pool_address
                FROM advanced_edge_evidence
                WHERE edge_type = ?
                ORDER BY as_of DESC, id DESC
                LIMIT 1
                """,
                (PROGRESS_EDGE_TYPE,),
            ).fetchone()
            if latest is None or not str(latest[0]).strip():
                return ()
            pool_address = str(latest[0]).strip()
            rows = conn.execute(
                """
                SELECT id, pool_address, as_of, evidence_json
                FROM advanced_edge_evidence
                WHERE edge_type = ?
                  AND pool_address = ?
                ORDER BY as_of DESC, id DESC
                LIMIT ?
                """,
                (PROGRESS_EDGE_TYPE, pool_address, limit),
            ).fetchall()
    except sqlite3.Error:
        return ()

    cycles = []
    for evidence_id, pool, as_of, evidence_json in rows:
        try:
            payload = json.loads(str(evidence_json))
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            # Malformed newest telemetry must break a rate-limit streak rather
            # than reusing older 429 evidence to disable a healthy timer.
            rate_limited = False
        else:
            rate_limited = _payload_rate_limited(payload)
        cycles.append(
            RateLimitCycle(
                evidence_id=int(evidence_id),
                pool_address=str(pool),
                as_of=str(as_of),
                rpc_rate_limited=rate_limited,
            )
        )
    return tuple(cycles)


def _consecutive_rate_limits(
    cycles: tuple[RateLimitCycle, ...],
    *,
    max_gap_seconds: int,
) -> int:
    if max_gap_seconds <= 0:
        raise ValueError("max_gap_seconds must be positive")

    count = 0
    previous: datetime | None = None
    for cycle in cycles:
        if not cycle.rpc_rate_limited:
            break
        try:
            when = _parse_time(cycle.as_of)
        except (TypeError, ValueError):
            break
        if previous is not None:
            gap = (previous - when).total_seconds()
            if gap < 0 or gap > max_gap_seconds:
                break
        count += 1
        previous = when
    return count


def _state(
    runner: SystemctlRunner,
    action: str,
) -> bool:
    completed = runner(
        ["systemctl", action, TIMER_UNIT],
        capture_output=True,
        text=True,
        check=False,
    )
    expected = "active" if action == "is-active" else "enabled"
    return bool(
        int(completed.returncode) == 0
        and (completed.stdout or "").strip() == expected
    )


def autopause(
    *,
    database_path: str | Path = "/opt/pio/data/pio.db",
    history_limit: int = 8,
    rate_limit_streak_threshold: int = 2,
    max_cycle_gap_seconds: int = 2700,
    max_latest_age_seconds: int = 2700,
    apply: bool = False,
    now: Now = lambda: datetime.now(timezone.utc),
    runner: SystemctlRunner = subprocess.run,
) -> Phase2RateLimitAutopauseReport:
    if history_limit <= 0:
        raise ValueError("history_limit must be positive")
    if rate_limit_streak_threshold <= 0:
        raise ValueError("rate_limit_streak_threshold must be positive")
    if max_latest_age_seconds <= 0:
        raise ValueError("max_latest_age_seconds must be positive")

    database = Path(database_path).expanduser()
    database_ready = database.is_file() and not database.is_symlink()
    cycles = (
        _read_cycles(database, limit=history_limit)
        if database_ready
        else ()
    )
    pool_address = cycles[0].pool_address if cycles else None
    streak = _consecutive_rate_limits(
        cycles,
        max_gap_seconds=max_cycle_gap_seconds,
    )
    repeated = streak >= rate_limit_streak_threshold

    latest_age: float | None = None
    latest_recent = False
    if cycles:
        try:
            latest_age = (
                now().astimezone(timezone.utc)
                - _parse_time(cycles[0].as_of)
            ).total_seconds()
        except (TypeError, ValueError):
            latest_age = None
        latest_recent = bool(
            latest_age is not None
            and 0 <= latest_age <= max_latest_age_seconds
        )

    enabled_before = _state(runner, "is-enabled")
    active_before = _state(runner, "is-active")
    pause_recommended = bool(
        database_ready
        and repeated
        and latest_recent
        and enabled_before
    )

    if not apply or not pause_recommended:
        return Phase2RateLimitAutopauseReport(
            database_ready=database_ready,
            database_path=str(database),
            pool_address=pool_address,
            cycles_checked=len(cycles),
            consecutive_rpc_rate_limited=streak,
            rate_limit_streak_threshold=rate_limit_streak_threshold,
            repeated_provider_rejection=repeated,
            latest_cycle_age_seconds=latest_age,
            latest_cycle_recent=latest_recent,
            timer_enabled_before=enabled_before,
            timer_active_before=active_before,
            pause_recommended=pause_recommended,
            apply_requested=apply,
            applied=False,
            timer_enabled_after=enabled_before,
            timer_active_after=active_before,
            future_timer_cycles_paused=not enabled_before,
            failure_step=None,
            read_only_evidence_check=True,
            rpc_called=False,
            database_write_performed=False,
            service_control_performed=False,
        )

    completed = runner(
        ["systemctl", "disable", "--now", TIMER_UNIT],
        capture_output=True,
        text=True,
        check=False,
    )
    if int(completed.returncode) != 0:
        enabled_after = _state(runner, "is-enabled")
        active_after = _state(runner, "is-active")
        return Phase2RateLimitAutopauseReport(
            database_ready=True,
            database_path=str(database),
            pool_address=pool_address,
            cycles_checked=len(cycles),
            consecutive_rpc_rate_limited=streak,
            rate_limit_streak_threshold=rate_limit_streak_threshold,
            repeated_provider_rejection=True,
            latest_cycle_age_seconds=latest_age,
            latest_cycle_recent=latest_recent,
            timer_enabled_before=enabled_before,
            timer_active_before=active_before,
            pause_recommended=True,
            apply_requested=True,
            applied=False,
            timer_enabled_after=enabled_after,
            timer_active_after=active_after,
            future_timer_cycles_paused=not enabled_after,
            failure_step="DISABLE_NOW",
            read_only_evidence_check=True,
            rpc_called=False,
            database_write_performed=False,
            service_control_performed=True,
        )

    enabled_after = _state(runner, "is-enabled")
    active_after = _state(runner, "is-active")
    applied = not enabled_after and not active_after
    return Phase2RateLimitAutopauseReport(
        database_ready=True,
        database_path=str(database),
        pool_address=pool_address,
        cycles_checked=len(cycles),
        consecutive_rpc_rate_limited=streak,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        repeated_provider_rejection=True,
        latest_cycle_age_seconds=latest_age,
        latest_cycle_recent=latest_recent,
        timer_enabled_before=enabled_before,
        timer_active_before=active_before,
        pause_recommended=True,
        apply_requested=True,
        applied=applied,
        timer_enabled_after=enabled_after,
        timer_active_after=active_after,
        future_timer_cycles_paused=not enabled_after,
        failure_step=None if applied else "VERIFY_PAUSED",
        read_only_evidence_check=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone local guard used after a failed Phase-2 evidence cycle. "
            "It reads persisted rate-limit telemetry from SQLite and may disable "
            "only the recurring evidence timer after repeated provider rejection. "
            "It loads no RPC environment and makes no network calls."
        )
    )
    parser.add_argument(
        "--database",
        default="/opt/pio/data/pio.db",
    )
    parser.add_argument("--history-limit", type=int, default=8)
    parser.add_argument("--rate-limit-streak-threshold", type=int, default=2)
    parser.add_argument("--max-cycle-gap-seconds", type=int, default=2700)
    parser.add_argument("--max-latest-age-seconds", type=int, default=2700)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = autopause(
        database_path=args.database,
        history_limit=args.history_limit,
        rate_limit_streak_threshold=args.rate_limit_streak_threshold,
        max_cycle_gap_seconds=args.max_cycle_gap_seconds,
        max_latest_age_seconds=args.max_latest_age_seconds,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))
    if args.apply and report.pause_recommended and not report.applied:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
