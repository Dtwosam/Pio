from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .paper_chain_collection import (
    PaperChainCollectionQueue,
    build_paper_chain_collection_queue,
)
from .paper_scheduler import PaperSchedulerState, paper_scheduler_state
from .quote_registry import TokenQuoteStatus, load_fresh_quote_map
from .storage import Storage


@dataclass(frozen=True)
class PaperHealthReport:
    account_id: str
    checked_at: str
    status: str
    open_positions: int
    last_tick_id: str | None
    last_tick_status: str | None
    last_tick_started_at: str | None
    last_tick_age_seconds: int | None
    scheduler: PaperSchedulerState
    chain_queue: PaperChainCollectionQueue
    quote_statuses: tuple[TokenQuoteStatus, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("health timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _required_token_y_mints(
    storage: Storage,
    *,
    account_id: str,
) -> tuple[str, ...]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT c.token_y_mint
            FROM paper_positions p
            JOIN paper_counterfactual_positions c
              ON c.position_id = p.position_id
            WHERE p.account_id = ?
              AND p.status = 'OPEN'
            ORDER BY c.token_y_mint ASC
            """,
            (account_id,),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def build_paper_health(
    storage: Storage,
    *,
    account_id: str,
    max_tick_age_seconds: int = 600,
    max_chain_age_seconds: int = 300,
    max_quote_age_seconds: int = 300,
    max_consecutive_failures: int = 2,
    array_radius: int = 1,
    as_of: str | None = None,
) -> PaperHealthReport:
    if not account_id.strip():
        raise ValueError("account_id is required")
    if max_tick_age_seconds < 0:
        raise ValueError("max_tick_age_seconds cannot be negative")
    if max_chain_age_seconds < 0:
        raise ValueError("max_chain_age_seconds cannot be negative")
    if max_quote_age_seconds < 0:
        raise ValueError("max_quote_age_seconds cannot be negative")
    if max_consecutive_failures <= 0:
        raise ValueError("max_consecutive_failures must be positive")

    now = _parse_time(as_of) if as_of is not None else datetime.now(timezone.utc)
    checked_at = now.isoformat()

    with storage.connect() as conn:
        account = conn.execute(
            "SELECT 1 FROM paper_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if account is None:
            raise ValueError(f"unknown paper account: {account_id}")
        open_positions = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM paper_positions
                WHERE account_id = ? AND status = 'OPEN'
                """,
                (account_id,),
            ).fetchone()[0]
        )
        last_tick = conn.execute(
            """
            SELECT tick_id, status, started_at
            FROM paper_ticks
            WHERE account_id = ?
            ORDER BY started_at DESC, tick_id DESC
            LIMIT 1
            """,
            (account_id,),
        ).fetchone()

    scheduler = paper_scheduler_state(storage, account_id=account_id)
    chain_queue = build_paper_chain_collection_queue(
        storage,
        account_id=account_id,
        max_age_seconds=max_chain_age_seconds,
        array_radius=array_radius,
        as_of=checked_at,
    )
    _, quote_statuses = load_fresh_quote_map(
        storage,
        token_mints=_required_token_y_mints(
            storage,
            account_id=account_id,
        ),
        max_age_seconds=max_quote_age_seconds,
        as_of=checked_at,
    )

    tick_id = str(last_tick[0]) if last_tick is not None else None
    tick_status = str(last_tick[1]) if last_tick is not None else None
    tick_started = str(last_tick[2]) if last_tick is not None else None
    tick_age = (
        max(0, int((now - _parse_time(tick_started)).total_seconds()))
        if tick_started is not None
        else None
    )

    critical: list[str] = []
    warning: list[str] = []

    if open_positions == 0:
        status = "IDLE"
    else:
        if last_tick is None:
            critical.append("no paper tick has been recorded for open positions")
        elif tick_age is not None and tick_age > max_tick_age_seconds:
            critical.append(
                f"last paper tick is stale by {tick_age} seconds"
            )

        if scheduler.consecutive_failures >= max_consecutive_failures:
            critical.append(
                "scheduler consecutive failure threshold reached: "
                f"{scheduler.consecutive_failures}"
            )

        if scheduler.owner_id is not None and scheduler.lease_until is not None:
            if _parse_time(scheduler.lease_until) <= now:
                critical.append("scheduler has an expired lease still assigned")

        if tick_status in {"FAILED", "MARKET_REFRESH_FAILED"}:
            warning.append(f"last paper tick status is {tick_status}")
        elif tick_status in {
            "PARTIAL",
            "WAITING_CHAIN",
            "WAITING_QUOTES",
        }:
            warning.append(f"last paper tick status is {tick_status}")

        if chain_queue.pools_needing_collection > 0:
            warning.append(
                f"{chain_queue.pools_needing_collection} open-position pool(s) "
                "need chain refresh"
            )

        stale_quotes = sum(not item.fresh for item in quote_statuses)
        if stale_quotes > 0:
            warning.append(
                f"{stale_quotes} required token quote(s) are stale or missing"
            )

        if critical:
            status = "UNHEALTHY"
        elif warning:
            status = "DEGRADED"
        else:
            status = "HEALTHY"

    return PaperHealthReport(
        account_id=account_id,
        checked_at=checked_at,
        status=status,
        open_positions=open_positions,
        last_tick_id=tick_id,
        last_tick_status=tick_status,
        last_tick_started_at=tick_started,
        last_tick_age_seconds=tick_age,
        scheduler=scheduler,
        chain_queue=chain_queue,
        quote_statuses=quote_statuses,
        reasons=tuple(critical + warning),
    )
