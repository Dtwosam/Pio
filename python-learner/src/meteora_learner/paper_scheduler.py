from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from .paper_tick import PaperTickReport, run_paper_tick
from .pool_safety import PoolSafetyConfig
from .position_policy import PositionManagementConfig
from .settings import Settings
from .storage import Storage, utc_now_iso


TickRunner = Callable[..., PaperTickReport]


@dataclass(frozen=True)
class PaperSchedulerState:
    account_id: str
    owner_id: str | None
    lease_until: str | None
    heartbeat_at: str | None
    last_tick_id: str | None
    last_started_at: str | None
    last_finished_at: str | None
    last_status: str | None
    consecutive_failures: int
    total_ticks: int
    updated_at: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScheduledPaperTickReport:
    account_id: str
    owner_id: str
    scheduled_bucket: str
    tick_id: str
    status: str
    lease_acquired: bool
    recovered_stale_lease: bool
    tick: PaperTickReport | None
    scheduler_state: PaperSchedulerState
    error: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("scheduler timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _time_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _bucket_start(now: datetime, interval_seconds: int) -> datetime:
    epoch = int(now.timestamp())
    bucket = epoch - (epoch % interval_seconds)
    return datetime.fromtimestamp(bucket, tz=timezone.utc)


def _tick_id(account_id: str, bucket: datetime) -> str:
    return f"paper-schedule:{account_id}:{int(bucket.timestamp())}"


def _record_scheduler_event(
    conn,
    *,
    account_id: str,
    event_time: str,
    event_type: str,
    tick_id: str,
    owner_id: str,
    status: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO paper_scheduler_events(
            account_id, event_time, event_type, tick_id,
            owner_id, status, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            event_time,
            event_type,
            tick_id,
            owner_id,
            status,
            json.dumps(details or {}, separators=(",", ":")),
        ),
    )


def paper_scheduler_state(
    storage: Storage,
    *,
    account_id: str,
) -> PaperSchedulerState:
    if not account_id.strip():
        raise ValueError("account_id is required")
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT account_id, owner_id, lease_until, heartbeat_at,
                   last_tick_id, last_started_at, last_finished_at,
                   last_status, consecutive_failures, total_ticks, updated_at
            FROM paper_scheduler_state
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
    if row is None:
        now = utc_now_iso()
        return PaperSchedulerState(
            account_id=account_id,
            owner_id=None,
            lease_until=None,
            heartbeat_at=None,
            last_tick_id=None,
            last_started_at=None,
            last_finished_at=None,
            last_status=None,
            consecutive_failures=0,
            total_ticks=0,
            updated_at=now,
        )
    return PaperSchedulerState(
        account_id=str(row[0]),
        owner_id=str(row[1]) if row[1] is not None else None,
        lease_until=str(row[2]) if row[2] is not None else None,
        heartbeat_at=str(row[3]) if row[3] is not None else None,
        last_tick_id=str(row[4]) if row[4] is not None else None,
        last_started_at=str(row[5]) if row[5] is not None else None,
        last_finished_at=str(row[6]) if row[6] is not None else None,
        last_status=str(row[7]) if row[7] is not None else None,
        consecutive_failures=int(row[8] or 0),
        total_ticks=int(row[9] or 0),
        updated_at=str(row[10]),
    )


def _acquire_lease(
    storage: Storage,
    *,
    account_id: str,
    owner_id: str,
    now: datetime,
    lease_seconds: int,
    tick_id: str,
) -> tuple[bool, bool]:
    now_text = _time_text(now)
    lease_until = _time_text(now + timedelta(seconds=lease_seconds))
    recovered = False

    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT owner_id, lease_until
            FROM paper_scheduler_state
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()

        if row is None:
            conn.execute(
                """
                INSERT INTO paper_scheduler_state(
                    account_id, owner_id, lease_until, heartbeat_at,
                    last_tick_id, last_started_at, last_status,
                    consecutive_failures, total_ticks, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', 0, 0, ?)
                """,
                (
                    account_id,
                    owner_id,
                    lease_until,
                    now_text,
                    tick_id,
                    now_text,
                    now_text,
                ),
            )
            _record_scheduler_event(
                conn,
                account_id=account_id,
                event_time=now_text,
                event_type="LEASE_ACQUIRED",
                tick_id=tick_id,
                owner_id=owner_id,
                status="RUNNING",
            )
            return True, False

        existing_owner = str(row[0]) if row[0] is not None else None
        existing_until = str(row[1]) if row[1] is not None else None
        if existing_owner is not None:
            if existing_until is not None and _parse_time(existing_until) > now:
                _record_scheduler_event(
                    conn,
                    account_id=account_id,
                    event_time=now_text,
                    event_type="LEASE_BUSY",
                    tick_id=tick_id,
                    owner_id=owner_id,
                    status="BUSY",
                    details={
                        "existing_owner_id": existing_owner,
                        "existing_lease_until": existing_until,
                    },
                )
                return False, False
            recovered = True

        conn.execute(
            """
            UPDATE paper_scheduler_state
            SET owner_id = ?, lease_until = ?, heartbeat_at = ?,
                last_tick_id = ?, last_started_at = ?,
                last_status = 'RUNNING', updated_at = ?
            WHERE account_id = ?
            """,
            (
                owner_id,
                lease_until,
                now_text,
                tick_id,
                now_text,
                now_text,
                account_id,
            ),
        )
        _record_scheduler_event(
            conn,
            account_id=account_id,
            event_time=now_text,
            event_type=(
                "LEASE_RECOVERED" if recovered else "LEASE_ACQUIRED"
            ),
            tick_id=tick_id,
            owner_id=owner_id,
            status="RUNNING",
            details=(
                {
                    "previous_owner_id": existing_owner,
                    "previous_lease_until": existing_until,
                }
                if recovered
                else None
            ),
        )
    return True, recovered


def _release_lease(
    storage: Storage,
    *,
    account_id: str,
    owner_id: str,
    status: str,
    finished_at: datetime,
) -> None:
    finished = _time_text(finished_at)
    failed = status in {"FAILED", "MARKET_REFRESH_FAILED", "PARTIAL"}
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT owner_id, consecutive_failures
            FROM paper_scheduler_state
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("paper scheduler state disappeared")
        if row[0] is None or str(row[0]) != owner_id:
            raise RuntimeError("paper scheduler lease ownership changed")

        previous_failures = int(row[1] or 0)
        conn.execute(
            """
            UPDATE paper_scheduler_state
            SET owner_id = NULL, lease_until = NULL,
                heartbeat_at = ?, last_finished_at = ?,
                last_status = ?, consecutive_failures = ?,
                total_ticks = total_ticks + 1, updated_at = ?
            WHERE account_id = ?
            """,
            (
                finished,
                finished,
                status,
                previous_failures + 1 if failed else 0,
                finished,
                account_id,
            ),
        )
        tick_row = conn.execute(
            """
            SELECT last_tick_id
            FROM paper_scheduler_state
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        if tick_row is None or tick_row[0] is None:
            raise RuntimeError("paper scheduler tick identity disappeared")
        _record_scheduler_event(
            conn,
            account_id=account_id,
            event_time=finished,
            event_type="TICK_FINISHED",
            tick_id=str(tick_row[0]),
            owner_id=owner_id,
            status=status,
        )


def run_scheduled_paper_tick(
    storage: Storage,
    *,
    account_id: str,
    interval_seconds: int = 300,
    lease_seconds: int = 900,
    owner_id: str | None = None,
    as_of: str | None = None,
    settings: Settings | None = None,
    chain_max_age_seconds: int = 300,
    quote_max_age_seconds: int = 300,
    array_radius: int = 1,
    max_positions: int | None = None,
    safety_config: PoolSafetyConfig = PoolSafetyConfig(),
    management_config: PositionManagementConfig = PositionManagementConfig(),
    retry_failed: bool = False,
    refresh_jupiter_quotes: bool = False,
    tick_runner: TickRunner = run_paper_tick,
) -> ScheduledPaperTickReport:
    """
    Run one cron/systemd-friendly PAPER scheduler invocation.

    A per-account SQLite lease prevents overlapping workers. The tick ID is
    deterministic for the time bucket, so retries inside the same bucket reuse
    the existing idempotent paper tick. Expired leases are safely taken over.
    """
    if not account_id.strip():
        raise ValueError("account_id is required")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    if lease_seconds <= interval_seconds:
        raise ValueError(
            "lease_seconds must exceed interval_seconds to prevent "
            "same-account worker overlap"
        )

    now = _parse_time(as_of) if as_of is not None else datetime.now(timezone.utc)
    bucket = _bucket_start(now, interval_seconds)
    tick_id = _tick_id(account_id, bucket)
    worker = owner_id or str(uuid4())

    acquired, recovered = _acquire_lease(
        storage,
        account_id=account_id,
        owner_id=worker,
        now=now,
        lease_seconds=lease_seconds,
        tick_id=tick_id,
    )
    if not acquired:
        state = paper_scheduler_state(storage, account_id=account_id)
        return ScheduledPaperTickReport(
            account_id=account_id,
            owner_id=worker,
            scheduled_bucket=_time_text(bucket),
            tick_id=tick_id,
            status="BUSY",
            lease_acquired=False,
            recovered_stale_lease=False,
            tick=None,
            scheduler_state=state,
            error=None,
        )

    tick: PaperTickReport | None = None
    status = "FAILED"
    error: str | None = None
    try:
        tick = tick_runner(
            storage,
            account_id=account_id,
            tick_id=tick_id,
            settings=settings,
            observed_at=_time_text(now),
            chain_max_age_seconds=chain_max_age_seconds,
            quote_max_age_seconds=quote_max_age_seconds,
            array_radius=array_radius,
            max_positions=max_positions,
            safety_config=safety_config,
            management_config=management_config,
            retry_failed=(retry_failed or recovered),
            refresh_jupiter_quotes=refresh_jupiter_quotes,
        )
        status = tick.status
        error = tick.error
    except Exception as exc:
        status = "FAILED"
        error = str(exc)[:2000]
    finally:
        _release_lease(
            storage,
            account_id=account_id,
            owner_id=worker,
            status=status,
            finished_at=max(datetime.now(timezone.utc), now),
        )

    return ScheduledPaperTickReport(
        account_id=account_id,
        owner_id=worker,
        scheduled_bucket=_time_text(bucket),
        tick_id=tick_id,
        status=status,
        lease_acquired=True,
        recovered_stale_lease=recovered,
        tick=tick,
        scheduler_state=paper_scheduler_state(
            storage,
            account_id=account_id,
        ),
        error=error,
    )
