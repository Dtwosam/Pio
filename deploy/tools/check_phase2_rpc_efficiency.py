#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import Any, Callable


PROGRESS_EDGE_TYPE = "PHASE2_EVIDENCE_CYCLE_PROGRESS_V1"
TIMER_UNIT = "pio-phase2-isolated-evidence-cycle.timer"
REINSPECTION_STAGE = "TRANSACTION_REINSPECTION"
PRESTATE_STAGE = "PRESTATE_VERIFICATION"

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]
Now = Callable[[], datetime]


@dataclass(frozen=True)
class DiscoveryCacheStatus:
    path: str
    exists: bool
    regular_file: bool
    symlink: bool
    format_valid: bool
    pool_matches: bool
    complete: bool
    captured_at: str | None
    age_seconds: float | None
    max_age_seconds: int
    reusable_now: bool
    positions_found: int | None
    positions_returned: int | None
    positions_cached: int | None


@dataclass(frozen=True)
class AttemptSummary:
    stage: str
    lookback_hours: int
    attempts: int
    succeeded: int
    failed: int
    rpc_rate_limited: int
    outcome_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class CycleSummary:
    evidence_id: int
    as_of: str
    status: str
    rpc_rate_limited: bool
    rpc_circuit_open: bool
    stages_failed: int
    stages_skipped: int


@dataclass(frozen=True)
class Phase2RpcEfficiencyReport:
    pool_address: str | None
    database_ready: bool
    database_path: str
    discovery_cache: DiscoveryCacheStatus
    position_attempts: AttemptSummary
    reinspection_attempts: AttemptSummary
    prestate_attempts: AttemptSummary
    recent_cycles: tuple[CycleSummary, ...]
    consecutive_rpc_rate_limited_cycles: int
    rate_limit_streak_threshold: int
    repeated_provider_rejection: bool
    timer_active: bool
    timer_enabled: bool
    timer_paused: bool
    pause_recommended: bool
    protected_from_future_timer_cycles: bool
    attention_required: bool
    collector_attempts_are_not_provider_credits: bool
    read_only: bool
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


def _read_safe_env(
    path: Path,
    *,
    keys: set[str],
) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in keys:
            continue
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        values[key] = value.strip()
    return values


def _cache_status(
    path: Path,
    *,
    pool_address: str | None,
    max_age_seconds: int,
    now: datetime,
) -> DiscoveryCacheStatus:
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds cannot be negative")

    exists = path.exists()
    symlink = path.is_symlink()
    regular = path.is_file() and not symlink
    if not regular:
        return DiscoveryCacheStatus(
            path=str(path),
            exists=exists,
            regular_file=regular,
            symlink=symlink,
            format_valid=False,
            pool_matches=False,
            complete=False,
            captured_at=None,
            age_seconds=None,
            max_age_seconds=max_age_seconds,
            reusable_now=False,
            positions_found=None,
            positions_returned=None,
            positions_cached=None,
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = None

    if not isinstance(payload, dict):
        return DiscoveryCacheStatus(
            path=str(path),
            exists=True,
            regular_file=True,
            symlink=False,
            format_valid=False,
            pool_matches=False,
            complete=False,
            captured_at=None,
            age_seconds=None,
            max_age_seconds=max_age_seconds,
            reusable_now=False,
            positions_found=None,
            positions_returned=None,
            positions_cached=None,
        )

    captured_at = payload.get("captured_at")
    cached_pool = payload.get("pool_address")
    discovery = payload.get("discovery")
    format_valid = bool(
        payload.get("format_version") == 1
        and isinstance(captured_at, str)
        and isinstance(cached_pool, str)
        and isinstance(discovery, dict)
        and isinstance(discovery.get("positions"), list)
    )
    if not format_valid:
        return DiscoveryCacheStatus(
            path=str(path),
            exists=True,
            regular_file=True,
            symlink=False,
            format_valid=False,
            pool_matches=False,
            complete=False,
            captured_at=(
                captured_at if isinstance(captured_at, str) else None
            ),
            age_seconds=None,
            max_age_seconds=max_age_seconds,
            reusable_now=False,
            positions_found=None,
            positions_returned=None,
            positions_cached=None,
        )

    positions = discovery["positions"]
    positions_found = int(discovery.get("positions_found", len(positions)))
    positions_returned = int(
        discovery.get("positions_returned", len(positions))
    )
    complete = bool(
        not discovery.get("truncated")
        and positions_found <= positions_returned
    )
    pool_matches = bool(
        pool_address
        and str(cached_pool) == pool_address
    )
    try:
        age = (
            now.astimezone(timezone.utc) - _parse_time(captured_at)
        ).total_seconds()
    except (TypeError, ValueError):
        age = None
    reusable = bool(
        format_valid
        and pool_matches
        and complete
        and max_age_seconds > 0
        and age is not None
        and 0 <= age <= max_age_seconds
    )
    return DiscoveryCacheStatus(
        path=str(path),
        exists=True,
        regular_file=True,
        symlink=False,
        format_valid=True,
        pool_matches=pool_matches,
        complete=complete,
        captured_at=captured_at,
        age_seconds=age,
        max_age_seconds=max_age_seconds,
        reusable_now=reusable,
        positions_found=positions_found,
        positions_returned=positions_returned,
        positions_cached=len(positions),
    )


def _empty_attempt(stage: str, lookback_hours: int) -> AttemptSummary:
    return AttemptSummary(
        stage=stage,
        lookback_hours=lookback_hours,
        attempts=0,
        succeeded=0,
        failed=0,
        rpc_rate_limited=0,
        outcome_counts=(),
    )


def _summarize_rows(
    *,
    stage: str,
    lookback_hours: int,
    rows: list[tuple[int, str | None, int]],
) -> AttemptSummary:
    attempts = sum(int(row[2]) for row in rows)
    succeeded = sum(
        int(count)
        for success, _outcome, count in rows
        if int(success) == 1
    )
    outcomes: dict[str, int] = {}
    for _success, outcome, count in rows:
        key = str(outcome or "SUCCESS")
        outcomes[key] = outcomes.get(key, 0) + int(count)
    rate_limited = sum(
        count
        for name, count in outcomes.items()
        if name == "RPC_RATE_LIMITED"
    )
    return AttemptSummary(
        stage=stage,
        lookback_hours=lookback_hours,
        attempts=attempts,
        succeeded=succeeded,
        failed=attempts - succeeded,
        rpc_rate_limited=rate_limited,
        outcome_counts=tuple(sorted(outcomes.items())),
    )


def _read_attempts(
    database_path: Path,
    *,
    pool_address: str | None,
    cutoff: str,
    lookback_hours: int,
) -> tuple[AttemptSummary, AttemptSummary, AttemptSummary]:
    position = _empty_attempt("POSITION_OBSERVATION", lookback_hours)
    reinspect = _empty_attempt(REINSPECTION_STAGE, lookback_hours)
    prestate = _empty_attempt(PRESTATE_STAGE, lookback_hours)

    if (
        not pool_address
        or database_path.is_symlink()
        or not database_path.is_file()
    ):
        return position, reinspect, prestate

    uri = f"file:{database_path.resolve()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            position_rows = conn.execute(
                """
                SELECT succeeded,
                       COALESCE(failure_category, 'SUCCESS'),
                       COUNT(*)
                FROM phase2_position_observation_attempts
                WHERE pool_address = ?
                  AND julianday(attempted_at) >= julianday(?)
                GROUP BY succeeded, COALESCE(failure_category, 'SUCCESS')
                """,
                (pool_address, cutoff),
            ).fetchall()

            task_rows = conn.execute(
                """
                SELECT stage, succeeded, outcome_category, COUNT(*)
                FROM phase2_collection_task_attempts
                WHERE stage IN (?, ?)
                  AND julianday(attempted_at) >= julianday(?)
                GROUP BY stage, succeeded, outcome_category
                """,
                (REINSPECTION_STAGE, PRESTATE_STAGE, cutoff),
            ).fetchall()
    except sqlite3.Error:
        return position, reinspect, prestate

    position = _summarize_rows(
        stage="POSITION_OBSERVATION",
        lookback_hours=lookback_hours,
        rows=[
            (int(success), str(outcome), int(count))
            for success, outcome, count in position_rows
        ],
    )
    for stage in (REINSPECTION_STAGE, PRESTATE_STAGE):
        summary = _summarize_rows(
            stage=stage,
            lookback_hours=lookback_hours,
            rows=[
                (int(success), str(outcome), int(count))
                for row_stage, success, outcome, count in task_rows
                if str(row_stage) == stage
            ],
        )
        if stage == REINSPECTION_STAGE:
            reinspect = summary
        else:
            prestate = summary
    return position, reinspect, prestate


def _read_cycles(
    database_path: Path,
    *,
    pool_address: str | None,
    limit: int,
) -> tuple[CycleSummary, ...]:
    if (
        not pool_address
        or limit <= 0
        or database_path.is_symlink()
        or not database_path.is_file()
    ):
        return ()

    uri = f"file:{database_path.resolve()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            rows = conn.execute(
                """
                SELECT id, as_of, status, evidence_json
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

    cycles: list[CycleSummary] = []
    for evidence_id, as_of, status, evidence_json in rows:
        try:
            payload = json.loads(str(evidence_json))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        outcomes = payload.get("stage_outcomes")
        inferred_rate_limit = False
        inferred_circuit = False
        if isinstance(outcomes, list):
            inferred_rate_limit = any(
                isinstance(item, list)
                and len(item) == 3
                and item[2] == "RPC_RATE_LIMITED"
                for item in outcomes
            )
            inferred_circuit = any(
                isinstance(item, list)
                and len(item) == 3
                and item[2] in {"RPC_RATE_LIMITED", "RPC_CIRCUIT_OPEN"}
                for item in outcomes
            )

        cycles.append(
            CycleSummary(
                evidence_id=int(evidence_id),
                as_of=str(as_of),
                status=str(status),
                rpc_rate_limited=bool(
                    payload.get("rpc_rate_limited", inferred_rate_limit)
                ),
                rpc_circuit_open=bool(
                    payload.get("rpc_circuit_open", inferred_circuit)
                ),
                stages_failed=int(payload.get("stages_failed", 0) or 0),
                stages_skipped=int(payload.get("stages_skipped", 0) or 0),
            )
        )
    return tuple(cycles)


def _consecutive_rate_limits(
    cycles: tuple[CycleSummary, ...],
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


def _systemctl_state(
    action: str,
    expected: str,
    *,
    runner: SystemctlRunner,
) -> bool:
    completed = runner(
        ["systemctl", action, TIMER_UNIT],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(
        int(completed.returncode) == 0
        and (completed.stdout or "").strip() == expected
    )


def inspect_rpc_efficiency(
    *,
    data_root: str | Path = "/opt/pio/data",
    env_file: str | Path = "/etc/pio/pio.env",
    lookback_hours: int = 24,
    cycle_history_limit: int = 8,
    rate_limit_streak_threshold: int = 2,
    max_cycle_gap_seconds: int = 2700,
    now: Now = lambda: datetime.now(timezone.utc),
    runner: SystemctlRunner = subprocess.run,
) -> Phase2RpcEfficiencyReport:
    if lookback_hours <= 0:
        raise ValueError("lookback_hours must be positive")
    if cycle_history_limit <= 0:
        raise ValueError("cycle_history_limit must be positive")
    if rate_limit_streak_threshold <= 0:
        raise ValueError("rate_limit_streak_threshold must be positive")

    current = now().astimezone(timezone.utc)
    root = Path(data_root).expanduser()
    database = root / "pio.db"
    database_ready = database.is_file() and not database.is_symlink()

    safe_env = _read_safe_env(
        Path(env_file).expanduser(),
        keys={
            "PIO_PHASE2_POSITION_POOL",
            "PIO_PHASE2_POSITION_DISCOVERY_CACHE_SECONDS",
        },
    )
    pool_address = safe_env.get("PIO_PHASE2_POSITION_POOL") or None
    try:
        cache_max_age = int(
            safe_env.get(
                "PIO_PHASE2_POSITION_DISCOVERY_CACHE_SECONDS",
                "3600",
            )
        )
    except ValueError:
        cache_max_age = 3600

    cache = _cache_status(
        root / "phase2-position-discovery-cache.json",
        pool_address=pool_address,
        max_age_seconds=cache_max_age,
        now=current,
    )

    cutoff = (
        current - timedelta(hours=lookback_hours)
    ).isoformat()
    position, reinspect, prestate = _read_attempts(
        database,
        pool_address=pool_address,
        cutoff=cutoff,
        lookback_hours=lookback_hours,
    )
    cycles = _read_cycles(
        database,
        pool_address=pool_address,
        limit=cycle_history_limit,
    )
    streak = _consecutive_rate_limits(
        cycles,
        max_gap_seconds=max_cycle_gap_seconds,
    )
    repeated = streak >= rate_limit_streak_threshold

    timer_active = _systemctl_state(
        "is-active",
        "active",
        runner=runner,
    )
    timer_enabled = _systemctl_state(
        "is-enabled",
        "enabled",
        runner=runner,
    )
    timer_paused = not timer_active and not timer_enabled
    # Future recurring work is possible whenever the timer remains enabled,
    # even if a point-in-time active query is transient or unusual.
    pause_recommended = bool(repeated and timer_enabled)
    protected = bool(not repeated or not timer_enabled)

    unsafe_cache = cache.symlink
    attention = bool(
        not database_ready
        or unsafe_cache
        or pause_recommended
    )

    return Phase2RpcEfficiencyReport(
        pool_address=pool_address,
        database_ready=database_ready,
        database_path=str(database),
        discovery_cache=cache,
        position_attempts=position,
        reinspection_attempts=reinspect,
        prestate_attempts=prestate,
        recent_cycles=cycles,
        consecutive_rpc_rate_limited_cycles=streak,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        repeated_provider_rejection=repeated,
        timer_active=timer_active,
        timer_enabled=timer_enabled,
        timer_paused=timer_paused,
        pause_recommended=pause_recommended,
        protected_from_future_timer_cycles=protected,
        attention_required=attention,
        collector_attempts_are_not_provider_credits=True,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect Phase-2 RPC-efficiency signals using only local DB, cache, "
            "environment metadata, and systemd state. This tool makes no RPC "
            "calls and does not estimate Helius credits from collector attempts."
        )
    )
    parser.add_argument("--data-root", default="/opt/pio/data")
    parser.add_argument("--env-file", default="/etc/pio/pio.env")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--cycle-history-limit", type=int, default=8)
    parser.add_argument("--rate-limit-streak-threshold", type=int, default=2)
    parser.add_argument("--max-cycle-gap-seconds", type=int, default=2700)
    args = parser.parse_args()

    report = inspect_rpc_efficiency(
        data_root=args.data_root,
        env_file=args.env_file,
        lookback_hours=args.lookback_hours,
        cycle_history_limit=args.cycle_history_limit,
        rate_limit_streak_threshold=args.rate_limit_streak_threshold,
        max_cycle_gap_seconds=args.max_cycle_gap_seconds,
    )
    print(json.dumps(report.to_record(), indent=2))
    if report.attention_required:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
