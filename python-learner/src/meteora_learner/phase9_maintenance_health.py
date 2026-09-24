from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .phase9_maintenance_history import (
    Phase9MaintenanceEvent,
    list_phase9_maintenance_events,
)
from .phase9_operation_lease import phase9_operation_lease_status
from .storage import Storage


DEGRADED_TERMINAL_STATUSES = {
    "FAILED",
    "NO_PROGRESS",
    "PARTIAL",
}

HEALTHY_TERMINAL_STATUSES = {
    "COMPLETE",
    "READY",
    "WAITING_INTERVAL",
    "MANUAL_REQUIRED",
    "MAX_STEPS",
}


@dataclass(frozen=True)
class Phase9MaintenanceHealthReport:
    operation_key: str
    as_of: str
    healthy: bool
    attention_required: bool
    status: str
    lease_active: bool
    lease_expired: bool
    lease_owner_id: str | None
    latest_event_id: int | None
    latest_event_time: str | None
    latest_activity: str | None
    latest_event_type: str | None
    latest_event_status: str | None
    latest_terminal_status: str | None
    seconds_since_latest_event: int | None
    recent_failure_events: int
    recent_busy_events: int
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Phase 9 maintenance timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _time_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _latest_terminal(
    events: tuple[Phase9MaintenanceEvent, ...],
) -> Phase9MaintenanceEvent | None:
    return next(
        (
            event
            for event in events
            if event.event_type == "RUN_FINISHED"
        ),
        None,
    )


def evaluate_phase9_maintenance_health(
    storage: Storage,
    *,
    operation_key: str = "phase9-research-maintenance",
    as_of: str | None = None,
    max_event_age_seconds: int = 10_800,
    history_limit: int = 100,
) -> Phase9MaintenanceHealthReport:
    if max_event_age_seconds <= 0:
        raise ValueError("max_event_age_seconds must be positive")
    if history_limit < 1 or history_limit > 500:
        raise ValueError("history_limit must be between 1 and 500")

    now = (
        _parse_time(as_of)
        if as_of is not None
        else datetime.now(timezone.utc)
    )
    now_text = _time_text(now)
    lease = phase9_operation_lease_status(
        storage,
        operation_key=operation_key,
        as_of=now_text,
    )
    events = list_phase9_maintenance_events(
        storage,
        operation_key=operation_key,
        as_of=now_text,
        limit=history_limit,
    )

    latest = events[0] if events else None
    latest_terminal = _latest_terminal(events)
    latest_age = (
        max(
            0,
            int(
                (now - _parse_time(latest.event_time)).total_seconds()
            ),
        )
        if latest is not None
        else None
    )

    recent_events = tuple(
        event
        for event in events
        if (
            now - _parse_time(event.event_time)
        ).total_seconds() <= max_event_age_seconds
    )
    recent_failures = sum(
        1
        for event in recent_events
        if (
            event.event_type == "RUN_FINISHED"
            and event.status in DEGRADED_TERMINAL_STATUSES
        )
    )
    recent_busy = sum(
        1
        for event in recent_events
        if event.event_type == "LEASE_BUSY"
    )

    reasons: list[str] = []
    healthy = False
    attention_required = True
    status = "NO_HISTORY"

    if lease.exists and lease.expired:
        status = "EXPIRED_LEASE"
        reasons.append(
            "the Phase 9 maintenance lease expired without being released"
        )
    elif lease.active:
        status = "RUNNING"
        if latest is None:
            reasons.append(
                "an active Phase 9 maintenance lease has no lifecycle journal"
            )
        elif latest_age is not None and latest_age > max_event_age_seconds:
            reasons.append(
                "the active Phase 9 maintenance lease has stale lifecycle history"
            )
        else:
            healthy = True
            attention_required = False
    elif latest is None:
        reasons.append("no Phase 9 maintenance lifecycle events exist")
    elif latest_age is not None and latest_age > max_event_age_seconds:
        status = "STALE"
        reasons.append(
            "the latest Phase 9 maintenance lifecycle event is older than "
            f"{max_event_age_seconds} seconds"
        )
    elif latest_terminal is None:
        status = "MISSING_TERMINAL"
        reasons.append(
            "maintenance lifecycle history has no terminal RUN_FINISHED event"
        )
    else:
        terminal_status = latest_terminal.status or "UNKNOWN"
        if terminal_status in DEGRADED_TERMINAL_STATUSES:
            status = "DEGRADED"
            reasons.append(
                "the latest Phase 9 maintenance run ended with "
                f"{terminal_status}"
            )
        elif terminal_status == "MANUAL_REQUIRED":
            status = "BLOCKED_MANUAL"
            healthy = True
            attention_required = True
            reasons.append(
                "automatic Phase 9 maintenance is healthy but explicit "
                "operator inputs are required"
            )
        elif terminal_status == "WAITING_INTERVAL":
            status = "WAITING_INTERVAL"
            healthy = True
            attention_required = False
        elif terminal_status in HEALTHY_TERMINAL_STATUSES:
            status = "HEALTHY"
            healthy = True
            attention_required = False
        else:
            status = "UNKNOWN_TERMINAL"
            reasons.append(
                "unrecognized Phase 9 maintenance terminal status: "
                f"{terminal_status}"
            )

    return Phase9MaintenanceHealthReport(
        operation_key=operation_key,
        as_of=now_text,
        healthy=healthy,
        attention_required=attention_required,
        status=status,
        lease_active=lease.active,
        lease_expired=lease.expired,
        lease_owner_id=lease.owner_id,
        latest_event_id=(latest.event_id if latest else None),
        latest_event_time=(latest.event_time if latest else None),
        latest_activity=(latest.activity if latest else None),
        latest_event_type=(latest.event_type if latest else None),
        latest_event_status=(latest.status if latest else None),
        latest_terminal_status=(
            latest_terminal.status if latest_terminal else None
        ),
        seconds_since_latest_event=latest_age,
        recent_failure_events=recent_failures,
        recent_busy_events=recent_busy,
        reasons=tuple(reasons),
    )
