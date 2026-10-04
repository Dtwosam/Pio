#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
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
class _DatabaseCompanionSnapshot:
    path: Path
    exists: bool
    opened: os.stat_result | None


@dataclass(frozen=True)
class _DatabasePathSnapshot:
    path: Path
    opened: os.stat_result
    companions: tuple[_DatabaseCompanionSnapshot, ...]


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


def _database_companion_snapshot(
    path: Path,
) -> _DatabaseCompanionSnapshot:
    try:
        opened = os.lstat(path)
    except FileNotFoundError:
        return _DatabaseCompanionSnapshot(
            path=path,
            exists=False,
            opened=None,
        )
    except OSError as exc:
        raise ValueError(
            "Phase-2 database companion cannot be inspected safely"
        ) from exc
    if stat.S_ISLNK(opened.st_mode) or not stat.S_ISREG(opened.st_mode):
        raise ValueError(
            "Phase-2 database companion must be a regular non-symlink file"
        )
    return _DatabaseCompanionSnapshot(
        path=path,
        exists=True,
        opened=opened,
    )


def _database_path_snapshot(
    database_path: Path,
) -> _DatabasePathSnapshot | None:
    raw = Path(database_path).expanduser()
    try:
        opened = os.lstat(raw)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("Phase-2 database cannot be inspected safely") from exc
    if stat.S_ISLNK(opened.st_mode) or not stat.S_ISREG(opened.st_mode):
        return None
    try:
        resolved = raw.resolve(strict=True)
    except OSError:
        return None

    companions = tuple(
        _database_companion_snapshot(
            resolved.with_name(resolved.name + suffix)
        )
        for suffix in ("-wal", "-shm")
    )
    return _DatabasePathSnapshot(
        path=resolved,
        opened=opened,
        companions=companions,
    )


def _assert_database_companion_stable(
    snapshot: _DatabaseCompanionSnapshot,
) -> None:
    try:
        current = os.lstat(snapshot.path)
    except FileNotFoundError:
        if snapshot.exists:
            raise ValueError(
                "Phase-2 database companion path changed before autopause"
            )
        return
    except OSError as exc:
        raise ValueError(
            "Phase-2 database companion path changed before autopause"
        ) from exc

    if not snapshot.exists:
        raise ValueError(
            "Phase-2 database companion path changed before autopause"
        )
    before = snapshot.opened
    assert before is not None
    if (
        not stat.S_ISREG(current.st_mode)
        or stat.S_ISLNK(current.st_mode)
        or current.st_dev != before.st_dev
        or current.st_ino != before.st_ino
        or current.st_mode != before.st_mode
    ):
        raise ValueError(
            "Phase-2 database companion path changed before autopause"
        )


def _assert_database_path_stable(
    snapshot: _DatabasePathSnapshot,
) -> None:
    try:
        current = os.stat(snapshot.path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(
            "Phase-2 database path changed before autopause"
        ) from exc
    before = snapshot.opened
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != before.st_dev
        or current.st_ino != before.st_ino
        or current.st_mode != before.st_mode
    ):
        raise ValueError("Phase-2 database path changed before autopause")
    for companion in snapshot.companions:
        _assert_database_companion_stable(companion)


def _same_database_identity(
    first: _DatabasePathSnapshot,
    second: _DatabasePathSnapshot,
) -> bool:
    return bool(
        first.path == second.path
        and first.opened.st_dev == second.opened.st_dev
        and first.opened.st_ino == second.opened.st_ino
        and first.opened.st_mode == second.opened.st_mode
    )


def _open_database_descriptor(
    snapshot: _DatabasePathSnapshot,
) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(snapshot.path, flags)
    except OSError as exc:
        raise ValueError(
            "Phase-2 database cannot be opened through a stable descriptor"
        ) from exc

    opened = os.fstat(fd)
    before = snapshot.opened
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_dev != before.st_dev
        or opened.st_ino != before.st_ino
        or opened.st_mode != before.st_mode
    ):
        os.close(fd)
        raise ValueError(
            "Phase-2 database path changed before descriptor capture"
        )
    return fd


def _assert_database_descriptor_stable(
    fd: int,
    snapshot: _DatabasePathSnapshot,
) -> None:
    try:
        current = os.fstat(fd)
    except OSError as exc:
        raise ValueError(
            "Phase-2 database descriptor changed before autopause"
        ) from exc
    before = snapshot.opened
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != before.st_dev
        or current.st_ino != before.st_ino
        or current.st_mode != before.st_mode
    ):
        raise ValueError(
            "Phase-2 database descriptor changed before autopause"
        )


def _read_cycles_snapshot(
    database_path: Path,
    *,
    limit: int,
) -> tuple[tuple[RateLimitCycle, ...], _DatabasePathSnapshot | None]:
    if limit <= 0:
        return (), None
    snapshot = _database_path_snapshot(database_path)
    if snapshot is None:
        return (), None

    fd = _open_database_descriptor(snapshot)
    try:
        proc_path = Path("/proc/self/fd") / str(fd)
        if not proc_path.exists():
            raise ValueError(
                "descriptor-bound SQLite path is unavailable on this platform"
            )
        uri = f"file:{proc_path}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=5) as conn:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("BEGIN")
                conn.execute(
                    "SELECT 1 FROM sqlite_master LIMIT 1"
                ).fetchone()
                _assert_database_descriptor_stable(fd, snapshot)
                _assert_database_path_stable(snapshot)

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
                    rows = []
                else:
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
                _assert_database_descriptor_stable(fd, snapshot)
                _assert_database_path_stable(snapshot)
                conn.rollback()
        except sqlite3.Error:
            rows = []
        _assert_database_descriptor_stable(fd, snapshot)
        _assert_database_path_stable(snapshot)
    finally:
        os.close(fd)

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
    _assert_database_path_stable(snapshot)
    return tuple(cycles), snapshot


def _read_cycles(
    database_path: Path,
    *,
    limit: int,
) -> tuple[RateLimitCycle, ...]:
    cycles, _snapshot = _read_cycles_snapshot(
        database_path,
        limit=limit,
    )
    return cycles


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


def _latest_cycle_age(
    cycles: tuple[RateLimitCycle, ...],
    *,
    now: Now,
) -> float | None:
    if not cycles:
        return None
    try:
        return (
            now().astimezone(timezone.utc)
            - _parse_time(cycles[0].as_of)
        ).total_seconds()
    except (TypeError, ValueError):
        return None


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


def _report_without_control(
    *,
    database_ready: bool,
    database: Path,
    pool_address: str | None,
    cycles: tuple[RateLimitCycle, ...],
    streak: int,
    rate_limit_streak_threshold: int,
    repeated: bool,
    latest_age: float | None,
    latest_recent: bool,
    enabled_before: bool,
    active_before: bool,
    pause_recommended: bool,
    apply: bool,
    failure_step: str | None,
    enabled_after: bool | None = None,
    active_after: bool | None = None,
) -> Phase2RateLimitAutopauseReport:
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
        timer_enabled_after=(
            enabled_before if enabled_after is None else enabled_after
        ),
        timer_active_after=(
            active_before if active_after is None else active_after
        ),
        future_timer_cycles_paused=not (
            enabled_before if enabled_after is None else enabled_after
        ),
        failure_step=failure_step,
        read_only_evidence_check=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
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
    try:
        cycles, database_snapshot = _read_cycles_snapshot(
            database,
            limit=history_limit,
        )
    except ValueError:
        cycles = ()
        database_snapshot = None
    database_ready = database_snapshot is not None
    pool_address = cycles[0].pool_address if cycles else None
    streak = _consecutive_rate_limits(
        cycles,
        max_gap_seconds=max_cycle_gap_seconds,
    )
    repeated = streak >= rate_limit_streak_threshold

    latest_age = _latest_cycle_age(cycles, now=now)
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
        return _report_without_control(
            database_ready=database_ready,
            database=database,
            pool_address=pool_address,
            cycles=cycles,
            streak=streak,
            rate_limit_streak_threshold=rate_limit_streak_threshold,
            repeated=repeated,
            latest_age=latest_age,
            latest_recent=latest_recent,
            enabled_before=enabled_before,
            active_before=active_before,
            pause_recommended=pause_recommended,
            apply=apply,
            failure_step=None,
        )

    assert database_snapshot is not None
    try:
        _assert_database_path_stable(database_snapshot)
        current_cycles, current_snapshot = _read_cycles_snapshot(
            database,
            limit=history_limit,
        )
    except ValueError:
        return _report_without_control(
            database_ready=True,
            database=database,
            pool_address=pool_address,
            cycles=cycles,
            streak=streak,
            rate_limit_streak_threshold=rate_limit_streak_threshold,
            repeated=repeated,
            latest_age=latest_age,
            latest_recent=latest_recent,
            enabled_before=enabled_before,
            active_before=active_before,
            pause_recommended=True,
            apply=True,
            failure_step="EVIDENCE_REVALIDATION",
        )

    if (
        current_snapshot is None
        or not _same_database_identity(
            database_snapshot,
            current_snapshot,
        )
        or current_cycles != cycles
    ):
        return _report_without_control(
            database_ready=True,
            database=database,
            pool_address=pool_address,
            cycles=cycles,
            streak=streak,
            rate_limit_streak_threshold=rate_limit_streak_threshold,
            repeated=repeated,
            latest_age=latest_age,
            latest_recent=latest_recent,
            enabled_before=enabled_before,
            active_before=active_before,
            pause_recommended=True,
            apply=True,
            failure_step="EVIDENCE_CHANGED_BEFORE_PAUSE",
        )

    revalidated_age = _latest_cycle_age(current_cycles, now=now)
    if (
        revalidated_age is None
        or revalidated_age < 0
        or revalidated_age > max_latest_age_seconds
    ):
        return _report_without_control(
            database_ready=True,
            database=database,
            pool_address=pool_address,
            cycles=cycles,
            streak=streak,
            rate_limit_streak_threshold=rate_limit_streak_threshold,
            repeated=repeated,
            latest_age=latest_age,
            latest_recent=latest_recent,
            enabled_before=enabled_before,
            active_before=active_before,
            pause_recommended=True,
            apply=True,
            failure_step="EVIDENCE_STALE_BEFORE_PAUSE",
        )

    enabled_at_boundary = _state(runner, "is-enabled")
    active_at_boundary = _state(runner, "is-active")
    if (
        enabled_at_boundary != enabled_before
        or active_at_boundary != active_before
    ):
        return _report_without_control(
            database_ready=True,
            database=database,
            pool_address=pool_address,
            cycles=cycles,
            streak=streak,
            rate_limit_streak_threshold=rate_limit_streak_threshold,
            repeated=repeated,
            latest_age=latest_age,
            latest_recent=latest_recent,
            enabled_before=enabled_before,
            active_before=active_before,
            pause_recommended=True,
            apply=True,
            failure_step="TIMER_STATE_CHANGED_BEFORE_PAUSE",
            enabled_after=enabled_at_boundary,
            active_after=active_at_boundary,
        )

    try:
        _assert_database_path_stable(database_snapshot)
    except ValueError:
        return _report_without_control(
            database_ready=True,
            database=database,
            pool_address=pool_address,
            cycles=cycles,
            streak=streak,
            rate_limit_streak_threshold=rate_limit_streak_threshold,
            repeated=repeated,
            latest_age=latest_age,
            latest_recent=latest_recent,
            enabled_before=enabled_before,
            active_before=active_before,
            pause_recommended=True,
            apply=True,
            failure_step="EVIDENCE_REVALIDATION",
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
            "It reads persisted rate-limit telemetry from a stable SQLite snapshot "
            "and may disable only the recurring evidence timer after repeated "
            "provider rejection. It loads no RPC environment and makes no "
            "network calls."
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
