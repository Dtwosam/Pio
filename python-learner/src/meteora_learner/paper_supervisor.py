from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .paper_chain_collection import (
    PaperChainCollectionQueue,
    build_paper_chain_collection_queue,
)
from .paper_portfolio import (
    PortfolioPaperCycleReport,
    run_portfolio_live_paper_cycle,
)
from .pool_safety import PoolSafetyConfig
from .position_policy import PositionManagementConfig
from .quote_registry import TokenQuoteStatus, load_fresh_quote_map
from .storage import Storage


@dataclass(frozen=True)
class PaperSupervisorReport:
    account_id: str
    cycle_id: str
    status: str
    chain_queue: PaperChainCollectionQueue
    quote_statuses: tuple[TokenQuoteStatus, ...]
    portfolio: PortfolioPaperCycleReport
    chain_blocked_pools: int
    quote_blocked_mints: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


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


def run_paper_supervisor(
    storage: Storage,
    *,
    account_id: str,
    cycle_id: str,
    chain_max_age_seconds: int = 300,
    quote_max_age_seconds: int = 300,
    array_radius: int = 1,
    max_positions: int | None = None,
    as_of: str | None = None,
    safety_config: PoolSafetyConfig = PoolSafetyConfig(),
    management_config: PositionManagementConfig = PositionManagementConfig(),
    retry_failed: bool = False,
) -> PaperSupervisorReport:
    """
    Run the safe portion of one paper cycle and expose unresolved dependencies.

    This function never executes Rust or fetches an external quote itself.
    Stale/missing chain state becomes inspect-pool work. Stale/missing quotes
    remain blockers. Eligible positions are processed through the idempotent
    latest-chain portfolio runner.
    """
    chain_queue = build_paper_chain_collection_queue(
        storage,
        account_id=account_id,
        max_age_seconds=chain_max_age_seconds,
        array_radius=array_radius,
        as_of=as_of,
    )
    mints = _required_token_y_mints(storage, account_id=account_id)
    _, quote_statuses = load_fresh_quote_map(
        storage,
        token_mints=mints,
        max_age_seconds=quote_max_age_seconds,
        as_of=as_of,
    )

    portfolio = run_portfolio_live_paper_cycle(
        storage,
        account_id=account_id,
        cycle_id=cycle_id,
        token_y_quotes=None,
        quote_max_age_seconds=quote_max_age_seconds,
        quote_as_of=as_of,
        chain_max_age_seconds=chain_max_age_seconds,
        chain_as_of=as_of,
        max_positions=max_positions,
        safety_config=safety_config,
        management_config=management_config,
        retry_failed=retry_failed,
    )

    quote_blocked = sum(not item.fresh for item in quote_statuses)
    chain_blocked = chain_queue.pools_needing_collection

    if portfolio.open_positions_seen == 0:
        status = "IDLE"
    elif portfolio.cycle is not None and portfolio.cycle.failed > 0:
        status = "PARTIAL_FAILURE"
    elif portfolio.scheduled_positions > 0 and portfolio.skipped_positions == 0:
        status = "COMPLETE"
    elif portfolio.scheduled_positions > 0:
        status = "PARTIAL"
    elif chain_blocked > 0:
        status = "WAITING_CHAIN"
    elif quote_blocked > 0:
        status = "WAITING_QUOTES"
    else:
        status = "NO_NEW_OBSERVATIONS"

    return PaperSupervisorReport(
        account_id=account_id,
        cycle_id=cycle_id,
        status=status,
        chain_queue=chain_queue,
        quote_statuses=quote_statuses,
        portfolio=portfolio,
        chain_blocked_pools=chain_blocked,
        quote_blocked_mints=quote_blocked,
    )
