from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .storage import Storage


@dataclass(frozen=True)
class Phase9OperationLease:
    operation_key: str
    owner_id: str
    acquired: bool
    recovered_stale_lease: bool
    lease_until: str | None
    existing_owner_id: str | None
    existing_lease_until: str | None

    def to_record(self) -> dict[str, object]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("lease timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _time_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def acquire_phase9_operation_lease(
    storage: Storage,
    *,
    operation_key: str,
    lease_seconds: int,
    owner_id: str | None = None,
    as_of: str | None = None,
) -> Phase9OperationLease:
    key = operation_key.strip()
    if not key:
        raise ValueError("operation_key is required")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")

    now = (
        _parse_time(as_of)
        if as_of is not None
        else datetime.now(timezone.utc)
    )
    now_text = _time_text(now)
    lease_until = _time_text(
        now + timedelta(seconds=lease_seconds)
    )
    owner = owner_id or str(uuid4())

    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT owner_id, lease_until
            FROM phase9_operation_leases
            WHERE operation_key = ?
            """,
            (key,),
        ).fetchone()

        existing_owner = (
            str(row[0]) if row is not None and row[0] is not None else None
        )
        existing_until = (
            str(row[1]) if row is not None and row[1] is not None else None
        )

        if (
            existing_owner is not None
            and existing_until is not None
            and _parse_time(existing_until) > now
        ):
            return Phase9OperationLease(
                operation_key=key,
                owner_id=owner,
                acquired=False,
                recovered_stale_lease=False,
                lease_until=None,
                existing_owner_id=existing_owner,
                existing_lease_until=existing_until,
            )

        recovered = row is not None
        conn.execute(
            """
            INSERT INTO phase9_operation_leases(
                operation_key, owner_id, lease_until,
                acquired_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(operation_key) DO UPDATE SET
                owner_id = excluded.owner_id,
                lease_until = excluded.lease_until,
                acquired_at = excluded.acquired_at,
                updated_at = excluded.updated_at
            """,
            (
                key,
                owner,
                lease_until,
                now_text,
                now_text,
            ),
        )

    return Phase9OperationLease(
        operation_key=key,
        owner_id=owner,
        acquired=True,
        recovered_stale_lease=recovered,
        lease_until=lease_until,
        existing_owner_id=existing_owner,
        existing_lease_until=existing_until,
    )


def release_phase9_operation_lease(
    storage: Storage,
    *,
    operation_key: str,
    owner_id: str,
) -> bool:
    key = operation_key.strip()
    owner = owner_id.strip()
    if not key:
        raise ValueError("operation_key is required")
    if not owner:
        raise ValueError("owner_id is required")

    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT owner_id
            FROM phase9_operation_leases
            WHERE operation_key = ?
            """,
            (key,),
        ).fetchone()
        if row is None:
            return False
        if str(row[0]) != owner:
            return False
        conn.execute(
            """
            DELETE FROM phase9_operation_leases
            WHERE operation_key = ?
              AND owner_id = ?
            """,
            (key, owner),
        )
    return True
