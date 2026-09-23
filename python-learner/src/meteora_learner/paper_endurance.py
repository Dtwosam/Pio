from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .storage import Storage


FAILURE_STATUSES = frozenset({"FAILED", "MARKET_REFRESH_FAILED"})
DEPENDENCY_BLOCKED_STATUSES = frozenset({"WAITING_CHAIN", "WAITING_QUOTES"})
TERMINAL_STATUSES = frozenset(
    {
        "COMPLETE",
        "PARTIAL",
        "WAITING_CHAIN",
        "WAITING_QUOTES",
        "NO_NEW_OBSERVATIONS",
        "IDLE",
        "MARKET_REFRESH_FAILED",
        "FAILED",
    }
)


@dataclass(frozen=True)
class PaperEnduranceCriteria:
    min_runtime_hours: float = 24.0
    min_terminal_ticks: int = 100
    min_success_rate_pct: float = 95.0
    max_dependency_blocked_pct: float = 10.0
    max_consecutive_failures: int = 2
    max_stale_running_ticks: int = 0
    stale_running_after_seconds: int = 900
    min_applied_chain_valuations: int = 24
    min_distinct_positions_valued: int = 1

    def validate(self) -> None:
        if self.min_runtime_hours < 0:
            raise ValueError("min_runtime_hours cannot be negative")
        if self.min_terminal_ticks < 0:
            raise ValueError("min_terminal_ticks cannot be negative")
        if not 0 <= self.min_success_rate_pct <= 100:
            raise ValueError("min_success_rate_pct must be between 0 and 100")
        if not 0 <= self.max_dependency_blocked_pct <= 100:
            raise ValueError(
                "max_dependency_blocked_pct must be between 0 and 100"
            )
        if self.max_consecutive_failures < 0:
            raise ValueError("max_consecutive_failures cannot be negative")
        if self.max_stale_running_ticks < 0:
            raise ValueError("max_stale_running_ticks cannot be negative")
        if self.stale_running_after_seconds <= 0:
            raise ValueError("stale_running_after_seconds must be positive")
        if self.min_applied_chain_valuations < 0:
            raise ValueError(
                "min_applied_chain_valuations cannot be negative"
            )
        if self.min_distinct_positions_valued < 0:
            raise ValueError(
                "min_distinct_positions_valued cannot be negative"
            )


@dataclass(frozen=True)
class PaperEnduranceReport:
    account_id: str
    checked_at: str
    passing: bool
    first_tick_started_at: str | None
    last_tick_finished_at: str | None
    runtime_hours: float
    total_ticks: int
    terminal_ticks: int
    status_counts: dict[str, int]
    failure_ticks: int
    dependency_blocked_ticks: int
    success_rate_pct: float
    dependency_blocked_pct: float
    max_observed_consecutive_failures: int
    stale_running_ticks: int
    applied_chain_valuations: int
    distinct_positions_valued: int
    lease_recoveries: int
    lease_busy_events: int
    criteria: PaperEnduranceCriteria
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("endurance timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _max_failure_streak(statuses: list[str]) -> int:
    current = 0
    maximum = 0
    for status in statuses:
        if status in FAILURE_STATUSES:
            current += 1
            maximum = max(maximum, current)
        elif status in TERMINAL_STATUSES:
            current = 0
    return maximum


def build_paper_endurance_report(
    storage: Storage,
    *,
    account_id: str,
    criteria: PaperEnduranceCriteria = PaperEnduranceCriteria(),
    as_of: str | None = None,
) -> PaperEnduranceReport:
    if not account_id.strip():
        raise ValueError("account_id is required")
    criteria.validate()

    now = (
        _parse_time(as_of)
        if as_of is not None
        else datetime.now(timezone.utc)
    )
    checked_at = now.isoformat()

    with storage.connect() as conn:
        account = conn.execute(
            "SELECT 1 FROM paper_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if account is None:
            raise ValueError(f"unknown paper account: {account_id}")

        tick_rows = conn.execute(
            """
            SELECT started_at, finished_at, status
            FROM paper_ticks
            WHERE account_id = ?
            ORDER BY started_at ASC, tick_id ASC
            """,
            (account_id,),
        ).fetchall()

        valuation_row = conn.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT v.position_id)
            FROM paper_chain_valuations v
            JOIN paper_positions p ON p.position_id = v.position_id
            WHERE p.account_id = ?
              AND v.status = 'APPLIED'
            """,
            (account_id,),
        ).fetchone()

        event_rows = conn.execute(
            """
            SELECT event_type, COUNT(*)
            FROM paper_scheduler_events
            WHERE account_id = ?
            GROUP BY event_type
            """,
            (account_id,),
        ).fetchall()

    statuses = [str(row[2]) for row in tick_rows]
    status_counts: dict[str, int] = {}
    for status in statuses:
        status_counts[status] = status_counts.get(status, 0) + 1

    terminal_ticks = sum(
        count
        for status, count in status_counts.items()
        if status in TERMINAL_STATUSES
    )
    failure_ticks = sum(
        status_counts.get(status, 0) for status in FAILURE_STATUSES
    )
    dependency_blocked_ticks = sum(
        status_counts.get(status, 0)
        for status in DEPENDENCY_BLOCKED_STATUSES
    )
    success_rate_pct = (
        100.0 * (terminal_ticks - failure_ticks) / terminal_ticks
        if terminal_ticks
        else 0.0
    )
    dependency_blocked_pct = (
        100.0 * dependency_blocked_ticks / terminal_ticks
        if terminal_ticks
        else 0.0
    )

    first_started = str(tick_rows[0][0]) if tick_rows else None
    finished_times = [
        str(row[1]) for row in tick_rows if row[1] is not None
    ]
    last_finished = finished_times[-1] if finished_times else None
    runtime_hours = 0.0
    if first_started is not None and last_finished is not None:
        runtime_hours = max(
            0.0,
            (
                _parse_time(last_finished) - _parse_time(first_started)
            ).total_seconds()
            / 3600.0,
        )

    stale_running_ticks = 0
    for started_at, finished_at, status in tick_rows:
        if str(status) != "RUNNING" or finished_at is not None:
            continue
        age = max(
            0,
            int((now - _parse_time(str(started_at))).total_seconds()),
        )
        if age > criteria.stale_running_after_seconds:
            stale_running_ticks += 1

    event_counts = {str(row[0]): int(row[1]) for row in event_rows}
    applied_valuations = int(valuation_row[0] or 0)
    positions_valued = int(valuation_row[1] or 0)
    max_failure_streak = _max_failure_streak(statuses)

    reasons: list[str] = []
    if runtime_hours < criteria.min_runtime_hours:
        reasons.append(
            f"runtime {runtime_hours:.2f}h is below "
            f"{criteria.min_runtime_hours:.2f}h"
        )
    if terminal_ticks < criteria.min_terminal_ticks:
        reasons.append(
            f"terminal ticks {terminal_ticks} are below "
            f"{criteria.min_terminal_ticks}"
        )
    if success_rate_pct < criteria.min_success_rate_pct:
        reasons.append(
            f"non-failure tick rate {success_rate_pct:.2f}% is below "
            f"{criteria.min_success_rate_pct:.2f}%"
        )
    if dependency_blocked_pct > criteria.max_dependency_blocked_pct:
        reasons.append(
            f"dependency-blocked tick rate "
            f"{dependency_blocked_pct:.2f}% exceeds "
            f"{criteria.max_dependency_blocked_pct:.2f}%"
        )
    if max_failure_streak > criteria.max_consecutive_failures:
        reasons.append(
            f"maximum consecutive failures {max_failure_streak} exceeds "
            f"{criteria.max_consecutive_failures}"
        )
    if stale_running_ticks > criteria.max_stale_running_ticks:
        reasons.append(
            f"stale RUNNING ticks {stale_running_ticks} exceed "
            f"{criteria.max_stale_running_ticks}"
        )
    if applied_valuations < criteria.min_applied_chain_valuations:
        reasons.append(
            f"applied chain valuations {applied_valuations} are below "
            f"{criteria.min_applied_chain_valuations}"
        )
    if positions_valued < criteria.min_distinct_positions_valued:
        reasons.append(
            f"distinct valued positions {positions_valued} are below "
            f"{criteria.min_distinct_positions_valued}"
        )

    return PaperEnduranceReport(
        account_id=account_id,
        checked_at=checked_at,
        passing=not reasons,
        first_tick_started_at=first_started,
        last_tick_finished_at=last_finished,
        runtime_hours=runtime_hours,
        total_ticks=len(tick_rows),
        terminal_ticks=terminal_ticks,
        status_counts=status_counts,
        failure_ticks=failure_ticks,
        dependency_blocked_ticks=dependency_blocked_ticks,
        success_rate_pct=success_rate_pct,
        dependency_blocked_pct=dependency_blocked_pct,
        max_observed_consecutive_failures=max_failure_streak,
        stale_running_ticks=stale_running_ticks,
        applied_chain_valuations=applied_valuations,
        distinct_positions_valued=positions_valued,
        lease_recoveries=event_counts.get("LEASE_RECOVERED", 0),
        lease_busy_events=event_counts.get("LEASE_BUSY", 0),
        criteria=criteria,
        reasons=tuple(reasons),
    )
