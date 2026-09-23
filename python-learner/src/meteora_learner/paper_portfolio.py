from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .paper_latest import (
    LatestPaperCycleItem,
    LatestPaperCycleReport,
    run_latest_live_paper_cycle,
)
from .pool_safety import PoolSafetyConfig
from .position_policy import PositionManagementConfig
from .research_store import ResearchStore
from .quote_registry import load_fresh_quote_map
from .storage import Storage


@dataclass(frozen=True)
class PortfolioScheduleItem:
    position_id: str
    pool_address: str
    token_y_mint: str
    last_observed_at: str
    latest_observed_at: str | None
    eligible: bool
    reason: str | None
    token_y_quote_per_atomic: float | None


@dataclass(frozen=True)
class PortfolioPaperCycleReport:
    account_id: str
    cycle_id: str
    open_positions_seen: int
    eligible_positions: int
    scheduled_positions: int
    skipped_positions: int
    schedule: tuple[PortfolioScheduleItem, ...]
    cycle: LatestPaperCycleReport | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _open_bound_positions(
    storage: Storage,
    *,
    account_id: str,
) -> list[dict[str, Any]]:
    with storage.connect() as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute(
            """
            SELECT p.position_id, p.pool_address, p.opened_at,
                   c.entry_observed_at, c.token_y_mint,
                   (
                       SELECT v.observed_at
                       FROM paper_chain_valuations v
                       WHERE v.position_id = p.position_id
                         AND v.status = 'APPLIED'
                       ORDER BY v.observed_at DESC
                       LIMIT 1
                   ) AS last_applied_observed_at
            FROM paper_positions p
            LEFT JOIN paper_counterfactual_positions c
              ON c.position_id = p.position_id
            WHERE p.account_id = ? AND p.status = 'OPEN'
            ORDER BY p.position_id ASC
            """,
            (account_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def run_portfolio_live_paper_cycle(
    storage: Storage,
    *,
    account_id: str,
    cycle_id: str,
    token_y_quotes: Mapping[str, float] | None = None,
    quote_max_age_seconds: int = 300,
    quote_as_of: str | None = None,
    max_positions: int | None = None,
    safety_config: PoolSafetyConfig = PoolSafetyConfig(),
    management_config: PositionManagementConfig = PositionManagementConfig(),
    retry_failed: bool = False,
) -> PortfolioPaperCycleReport:
    """
    Discover and process chain-bound open paper positions for one account.

    Positions without a chain binding, current token-Y quote, or new chain
    observation are reported and skipped. Eligible positions are prioritized by
    oldest last-applied observation, then position_id.
    """
    if not account_id.strip():
        raise ValueError("account_id is required")
    if not cycle_id.strip():
        raise ValueError("cycle_id is required")
    if max_positions is not None and max_positions <= 0:
        raise ValueError("max_positions must be positive when supplied")

    rows = _open_bound_positions(storage, account_id=account_id)
    store = ResearchStore(storage.path)
    registry_status: dict[str, Any] = {}
    if token_y_quotes is None:
        mints = [
            str(row["token_y_mint"])
            for row in rows
            if row.get("token_y_mint") is not None
        ]
        resolved_quotes, statuses = load_fresh_quote_map(
            storage,
            token_mints=mints,
            max_age_seconds=quote_max_age_seconds,
            as_of=quote_as_of,
        )
        token_y_quotes = resolved_quotes
        registry_status = {item.token_mint: item for item in statuses}

    scheduled: list[tuple[PortfolioScheduleItem, LatestPaperCycleItem]] = []
    report_items: list[PortfolioScheduleItem] = []

    for row in rows:
        position_id = str(row["position_id"])
        pool_address = str(row["pool_address"])
        entry_at = row.get("entry_observed_at")
        token_y_mint = row.get("token_y_mint")

        if entry_at is None or token_y_mint is None:
            report_items.append(
                PortfolioScheduleItem(
                    position_id=position_id,
                    pool_address=pool_address,
                    token_y_mint="",
                    last_observed_at=str(row["opened_at"]),
                    latest_observed_at=None,
                    eligible=False,
                    reason="paper position is not chain-bound",
                    token_y_quote_per_atomic=None,
                )
            )
            continue

        times = store.chain_observation_times(
            pool_address,
            limit=1,
            ascending=False,
        )
        latest = times[0] if times else None
        last = str(row.get("last_applied_observed_at") or entry_at)
        quote_raw = token_y_quotes.get(str(token_y_mint))
        quote = float(quote_raw) if quote_raw is not None else None

        reason: str | None = None
        if latest is None:
            reason = "pool has no chain observation"
        elif latest <= last:
            reason = "no new chain observation"
        elif quote is None:
            status = registry_status.get(str(token_y_mint))
            reason = (
                f"token-Y quote unavailable for mint {token_y_mint}: "
                f"{status.reason}"
                if status is not None and status.reason
                else f"missing token-Y quote for mint {token_y_mint}"
            )
        elif quote <= 0:
            reason = f"token-Y quote for mint {token_y_mint} must be positive"

        item = PortfolioScheduleItem(
            position_id=position_id,
            pool_address=pool_address,
            token_y_mint=str(token_y_mint),
            last_observed_at=last,
            latest_observed_at=latest,
            eligible=reason is None,
            reason=reason,
            token_y_quote_per_atomic=quote,
        )
        report_items.append(item)

        if reason is None:
            scheduled.append(
                (
                    item,
                    LatestPaperCycleItem(
                        position_id=position_id,
                        token_y_quote_per_atomic=quote,
                    ),
                )
            )

    scheduled.sort(
        key=lambda pair: (
            pair[0].last_observed_at,
            pair[0].position_id,
        )
    )
    eligible_count = len(scheduled)
    if max_positions is not None:
        scheduled = scheduled[:max_positions]

    selected_ids = {pair[0].position_id for pair in scheduled}
    if max_positions is not None and eligible_count > len(scheduled):
        updated: list[PortfolioScheduleItem] = []
        for item in report_items:
            if item.eligible and item.position_id not in selected_ids:
                updated.append(
                    PortfolioScheduleItem(
                        position_id=item.position_id,
                        pool_address=item.pool_address,
                        token_y_mint=item.token_y_mint,
                        last_observed_at=item.last_observed_at,
                        latest_observed_at=item.latest_observed_at,
                        eligible=True,
                        reason="eligible but deferred by max_positions cap",
                        token_y_quote_per_atomic=item.token_y_quote_per_atomic,
                    )
                )
            else:
                updated.append(item)
        report_items = updated

    cycle = None
    if scheduled:
        cycle = run_latest_live_paper_cycle(
            storage,
            cycle_id=cycle_id,
            items=tuple(pair[1] for pair in scheduled),
            safety_config=safety_config,
            management_config=management_config,
            retry_failed=retry_failed,
        )

    return PortfolioPaperCycleReport(
        account_id=account_id,
        cycle_id=cycle_id,
        open_positions_seen=len(rows),
        eligible_positions=eligible_count,
        scheduled_positions=len(scheduled),
        skipped_positions=len(rows) - len(scheduled),
        schedule=tuple(report_items),
        cycle=cycle,
    )
