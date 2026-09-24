from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .storage import Storage


PHASE9_MAINTENANCE_EVENT_TYPES = {
    "LEASE_ACQUIRED",
    "LEASE_BUSY",
    "LEASE_RECOVERED",
    "RUN_FINISHED",
}


@dataclass(frozen=True)
class Phase9MaintenanceEvent:
    event_id: int
    event_time: str
    operation_key: str
    activity: str
    owner_id: str
    event_type: str
    status: str | None
    details: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _normalize_time(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("event_time must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat()


def record_phase9_maintenance_event(
    storage: Storage,
    *,
    operation_key: str,
    activity: str,
    owner_id: str,
    event_type: str,
    status: str | None = None,
    details: dict[str, Any] | None = None,
    event_time: str | None = None,
) -> Phase9MaintenanceEvent:
    key = _normalize_text(operation_key, field="operation_key")
    activity_text = _normalize_text(activity, field="activity")
    owner = _normalize_text(owner_id, field="owner_id")
    kind = _normalize_text(event_type, field="event_type")
    if kind not in PHASE9_MAINTENANCE_EVENT_TYPES:
        raise ValueError(f"unsupported Phase 9 maintenance event_type: {kind}")
    status_text = status.strip() if status is not None else None
    if status_text == "":
        status_text = None
    timestamp = _normalize_time(event_time)
    payload = details or {}
    details_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    with storage.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO phase9_maintenance_events(
                event_time,
                operation_key,
                activity,
                owner_id,
                event_type,
                status,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                key,
                activity_text,
                owner,
                kind,
                status_text,
                details_json,
            ),
        )
        event_id = int(cursor.lastrowid)

    return Phase9MaintenanceEvent(
        event_id=event_id,
        event_time=timestamp,
        operation_key=key,
        activity=activity_text,
        owner_id=owner,
        event_type=kind,
        status=status_text,
        details=payload,
    )


def list_phase9_maintenance_events(
    storage: Storage,
    *,
    operation_key: str = "phase9-research-maintenance",
    activity: str | None = None,
    limit: int = 20,
) -> tuple[Phase9MaintenanceEvent, ...]:
    key = _normalize_text(operation_key, field="operation_key")
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    activity_text = (
        _normalize_text(activity, field="activity")
        if activity is not None
        else None
    )

    query = """
        SELECT
            id,
            event_time,
            operation_key,
            activity,
            owner_id,
            event_type,
            status,
            details_json
        FROM phase9_maintenance_events
        WHERE operation_key = ?
    """
    params: list[object] = [key]
    if activity_text is not None:
        query += " AND activity = ?"
        params.append(activity_text)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with storage.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()

    events = []
    for row in rows:
        details = json.loads(str(row[7]))
        if not isinstance(details, dict):
            raise ValueError(
                "Phase 9 maintenance event details must decode to an object"
            )
        events.append(
            Phase9MaintenanceEvent(
                event_id=int(row[0]),
                event_time=str(row[1]),
                operation_key=str(row[2]),
                activity=str(row[3]),
                owner_id=str(row[4]),
                event_type=str(row[5]),
                status=(str(row[6]) if row[6] is not None else None),
                details=details,
            )
        )
    return tuple(events)
