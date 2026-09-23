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
    lease_recoveries: int
    lease_busy_events: int
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
        scheduler_event_rows = conn.execute(
            """
            SELECT event_type, COUNT(*)
            FROM paper_scheduler_events
            WHERE account_id = ?
            GROUP BY event_type
            """,
            (account_id,),
        ).fetchall()

    scheduler_event_counts = {
        str(row[0]): int(row[1]) for row in scheduler_event_rows
    }
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

        if scheduler.owner_id is not None:
            if scheduler.lease_until is None:
                critical.append(
                    "scheduler owner is assigned without a lease deadline"
                )
            elif _parse_time(scheduler.lease_until) <= now:
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
        lease_recoveries=scheduler_event_counts.get("LEASE_RECOVERED", 0),
        lease_busy_events=scheduler_event_counts.get("LEASE_BUSY", 0),
        chain_queue=chain_queue,
        quote_statuses=quote_statuses,
        reasons=tuple(critical + warning),
    )



def _prom_label(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def render_paper_health_prometheus(report: PaperHealthReport) -> str:
    account = _prom_label(report.account_id)
    statuses = ("HEALTHY", "DEGRADED", "UNHEALTHY", "IDLE")
    stale_quotes = sum(not item.fresh for item in report.quote_statuses)
    lease_active = int(
        report.scheduler.owner_id is not None
        and report.scheduler.lease_until is not None
    )
    lines = [
        "# HELP pio_paper_health_status Current PAPER health state as a one-hot gauge.",
        "# TYPE pio_paper_health_status gauge",
    ]
    for status in statuses:
        lines.append(
            'pio_paper_health_status'
            f'{{account="{account}",status="{status}"}} '
            f'{1 if report.status == status else 0}'
        )
    lines.extend(
        [
            "# HELP pio_paper_open_positions Number of open paper positions.",
            "# TYPE pio_paper_open_positions gauge",
            f'pio_paper_open_positions{{account="{account}"}} {report.open_positions}',
            "# HELP pio_paper_scheduler_consecutive_failures Consecutive failed/degraded scheduler ticks.",
            "# TYPE pio_paper_scheduler_consecutive_failures gauge",
            (
                f'pio_paper_scheduler_consecutive_failures{{account="{account}"}} '
                f'{report.scheduler.consecutive_failures}'
            ),
            "# HELP pio_paper_scheduler_total_ticks Total scheduler invocations recorded.",
            "# TYPE pio_paper_scheduler_total_ticks counter",
            (
                f'pio_paper_scheduler_total_ticks{{account="{account}"}} '
                f'{report.scheduler.total_ticks}'
            ),
            "# HELP pio_paper_scheduler_lease_active Whether a scheduler lease is currently assigned.",
            "# TYPE pio_paper_scheduler_lease_active gauge",
            f'pio_paper_scheduler_lease_active{{account="{account}"}} {lease_active}',
            "# HELP pio_paper_scheduler_lease_recoveries_total Persisted stale-lease recovery events.",
            "# TYPE pio_paper_scheduler_lease_recoveries_total counter",
            (
                f'pio_paper_scheduler_lease_recoveries_total{{account="{account}"}} '
                f'{report.lease_recoveries}'
            ),
            "# HELP pio_paper_scheduler_lease_busy_total Persisted overlapping-worker lease refusals.",
            "# TYPE pio_paper_scheduler_lease_busy_total counter",
            (
                f'pio_paper_scheduler_lease_busy_total{{account="{account}"}} '
                f'{report.lease_busy_events}'
            ),
            "# HELP pio_paper_chain_pools_needing_refresh Open-position pools with stale/missing chain state.",
            "# TYPE pio_paper_chain_pools_needing_refresh gauge",
            (
                f'pio_paper_chain_pools_needing_refresh{{account="{account}"}} '
                f'{report.chain_queue.pools_needing_collection}'
            ),
            "# HELP pio_paper_quotes_stale_or_missing Required token quotes that are stale or missing.",
            "# TYPE pio_paper_quotes_stale_or_missing gauge",
            f'pio_paper_quotes_stale_or_missing{{account="{account}"}} {stale_quotes}',
        ]
    )
    if report.last_tick_age_seconds is not None:
        lines.extend(
            [
                "# HELP pio_paper_last_tick_age_seconds Age of the latest paper tick.",
                "# TYPE pio_paper_last_tick_age_seconds gauge",
                (
                    f'pio_paper_last_tick_age_seconds{{account="{account}"}} '
                    f'{report.last_tick_age_seconds}'
                ),
            ]
        )
    return "\n".join(lines) + "\n"
