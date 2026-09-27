from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Any, Callable

from .jupiter_quotes import refresh_jupiter_quotes_for_mints
from .market_paper_intake import (
    MarketPaperIntakeReport,
    build_market_paper_intake,
)
from .paper_account import paper_account_snapshot
from .paper_empirical_entry_workflow import (
    EmpiricalPaperEntryWorkflowReport,
    run_empirical_paper_entry_workflow,
)
from .quote_registry import TokenQuoteStatus, token_quote_status
from .research_store import ResearchStore
from .storage import Storage, utc_now_iso


IntakeRunner = Callable[..., MarketPaperIntakeReport]
EntryRunner = Callable[..., EmpiricalPaperEntryWorkflowReport]
QuoteStatusLoader = Callable[..., TokenQuoteStatus]
QuoteRefresher = Callable[..., tuple[Any, ...]]


@dataclass(frozen=True)
class MarketPaperExplorationItem:
    pool_address: str
    token_y_mint: str | None
    decision_observed_at: str | None
    status: str
    position_id: str | None
    amount_y_atomic: int | None
    network_cost_y_atomic: int | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketPaperExplorationReport:
    account_id: str
    run_id: str
    observed_at: str
    paper_only: bool
    policy_actionable: bool
    live_authorized: bool
    pools_ready: int
    pools_considered: int
    positions_opened: int
    max_new_positions: int
    items: tuple[MarketPaperExplorationItem, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _open_pools(storage: Storage, *, account_id: str) -> set[str]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT pool_address
            FROM paper_positions
            WHERE account_id = ? AND status = 'OPEN'
            """,
            (account_id,),
        ).fetchall()
    return {str(row[0]) for row in rows}


def _chain_pool_at(
    storage: Storage,
    *,
    pool_address: str,
    observed_at: str,
) -> dict[str, Any] | None:
    return ResearchStore(storage.path).chain_pool_snapshot_at(
        pool_address,
        observed_at,
    )


def _atomic_budget(
    *,
    capital_quote: Decimal,
    network_cost_quote: Decimal,
    quote_per_atomic: Decimal,
) -> tuple[int, int]:
    if quote_per_atomic <= 0:
        raise ValueError("quote_per_atomic must be positive")
    amount_y = int(capital_quote / quote_per_atomic)
    if amount_y <= 0:
        raise ValueError("capital is too small for one token-Y atomic unit")
    network = (
        int(
            (network_cost_quote / quote_per_atomic).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        if network_cost_quote > 0
        else 0
    )
    return amount_y, network


def run_market_paper_exploration(
    storage: Storage,
    *,
    account_id: str,
    run_id: str,
    per_position_capital_quote: float,
    network_cost_quote: float,
    max_new_positions: int = 1,
    minimum_chain_observations: int = 12,
    intake_max_pools: int = 500,
    quote_max_age_seconds: int = 300,
    max_share_bps: int = 500,
    intake_runner: IntakeRunner = build_market_paper_intake,
    quote_refresher: QuoteRefresher = refresh_jupiter_quotes_for_mints,
    quote_status_loader: QuoteStatusLoader = token_quote_status,
    entry_runner: EntryRunner = run_empirical_paper_entry_workflow,
) -> MarketPaperExplorationReport:
    """
    Open a bounded number of new virtual PAPER positions from ready market pools.

    Pool ordering comes directly from the neutral intake queue. This function
    never ranks by TVL, fees, APR, forecast, or expected return.
    """
    if not account_id.strip():
        raise ValueError("account_id is required")
    if not run_id.strip():
        raise ValueError("run_id is required")
    if max_new_positions < 1:
        raise ValueError("max_new_positions must be positive")
    if quote_max_age_seconds < 0:
        raise ValueError("quote_max_age_seconds cannot be negative")
    capital = Decimal(str(per_position_capital_quote))
    network_cost = Decimal(str(network_cost_quote))
    if not capital.is_finite() or capital <= 0:
        raise ValueError("per_position_capital_quote must be positive and finite")
    if not network_cost.is_finite() or network_cost < 0:
        raise ValueError("network_cost_quote must be non-negative and finite")

    paper_account_snapshot(storage, account_id=account_id)
    observed_at = utc_now_iso()
    intake = intake_runner(
        storage.path,
        minimum_chain_observations=minimum_chain_observations,
        max_pools=intake_max_pools,
    )
    if (
        not intake.research_only
        or not intake.paper_only
        or intake.policy_actionable
        or intake.execution_wired
    ):
        raise RuntimeError("market PAPER intake crossed safety boundary")

    open_pools = _open_pools(storage, account_id=account_id)
    ready = [
        item
        for item in intake.pools
        if item.ready_for_candidate_cycle
    ]

    contexts: dict[str, tuple[str, str]] = {}
    items: list[MarketPaperExplorationItem] = []
    for item in ready:
        pool_address = item.pool_address
        if pool_address in open_pools:
            items.append(
                MarketPaperExplorationItem(
                    pool_address=pool_address,
                    token_y_mint=None,
                    decision_observed_at=None,
                    status="ALREADY_OPEN",
                    position_id=None,
                    amount_y_atomic=None,
                    network_cost_y_atomic=None,
                )
            )
            continue
        decision_at = item.latest_chain_observed_at
        if decision_at is None:
            items.append(
                MarketPaperExplorationItem(
                    pool_address=pool_address,
                    token_y_mint=None,
                    decision_observed_at=None,
                    status="CHAIN_CONTEXT_MISSING",
                    position_id=None,
                    amount_y_atomic=None,
                    network_cost_y_atomic=None,
                )
            )
            continue
        pool = _chain_pool_at(
            storage,
            pool_address=pool_address,
            observed_at=decision_at,
        )
        if pool is None:
            items.append(
                MarketPaperExplorationItem(
                    pool_address=pool_address,
                    token_y_mint=None,
                    decision_observed_at=decision_at,
                    status="CHAIN_CONTEXT_MISSING",
                    position_id=None,
                    amount_y_atomic=None,
                    network_cost_y_atomic=None,
                )
            )
            continue
        token_y_mint = str(pool.get("token_y_mint", "")).strip()
        if not token_y_mint:
            items.append(
                MarketPaperExplorationItem(
                    pool_address=pool_address,
                    token_y_mint=None,
                    decision_observed_at=decision_at,
                    status="TOKEN_Y_MINT_MISSING",
                    position_id=None,
                    amount_y_atomic=None,
                    network_cost_y_atomic=None,
                )
            )
            continue
        contexts[pool_address] = (decision_at, token_y_mint)

    quote_refresher(
        storage,
        token_mints={mint for _, mint in contexts.values()},
        observed_at=observed_at,
    )

    opened = 0
    considered = 0
    for item in ready:
        if opened >= max_new_positions:
            break
        pool_address = item.pool_address
        context = contexts.get(pool_address)
        if context is None or pool_address in open_pools:
            continue
        considered += 1
        decision_at, token_y_mint = context
        quote = quote_status_loader(
            storage,
            token_mint=token_y_mint,
            max_age_seconds=quote_max_age_seconds,
            as_of=observed_at,
        )
        if not quote.fresh or quote.quote_per_atomic is None:
            items.append(
                MarketPaperExplorationItem(
                    pool_address=pool_address,
                    token_y_mint=token_y_mint,
                    decision_observed_at=decision_at,
                    status="TOKEN_Y_QUOTE_UNAVAILABLE",
                    position_id=None,
                    amount_y_atomic=None,
                    network_cost_y_atomic=None,
                )
            )
            continue

        try:
            amount_y, network_atomic = _atomic_budget(
                capital_quote=capital,
                network_cost_quote=network_cost,
                quote_per_atomic=Decimal(str(quote.quote_per_atomic)),
            )
            position_id = (
                f"empirical:{account_id}:{run_id}:{pool_address}"
            )
            event_key = (
                f"empirical-enter:{account_id}:{run_id}:{pool_address}"
            )

            with storage.connect() as conn:
                existing = conn.execute(
                    "SELECT status FROM paper_positions WHERE position_id = ?",
                    (position_id,),
                ).fetchone()
            if existing is not None:
                status = "ALREADY_APPLIED"
                if str(existing[0]) == "OPEN":
                    opened += 1
                items.append(
                    MarketPaperExplorationItem(
                        pool_address=pool_address,
                        token_y_mint=token_y_mint,
                        decision_observed_at=decision_at,
                        status=status,
                        position_id=position_id,
                        amount_y_atomic=amount_y,
                        network_cost_y_atomic=network_atomic,
                    )
                )
                continue

            entry = entry_runner(
                storage,
                account_id=account_id,
                position_id=position_id,
                event_key=event_key,
                pool_address=pool_address,
                amount_x=0,
                amount_y=amount_y,
                capital_quote=float(capital),
                network_cost_y_atomic=network_atomic,
                entry_cost_quote=float(network_cost),
                max_share_bps=max_share_bps,
                as_of=decision_at,
            )
            if (
                not entry.paper_only
                or entry.live_authorized
            ):
                raise RuntimeError("empirical PAPER entry crossed safety boundary")
            status = entry.status
            if status == "OPENED":
                opened += 1
            items.append(
                MarketPaperExplorationItem(
                    pool_address=pool_address,
                    token_y_mint=token_y_mint,
                    decision_observed_at=decision_at,
                    status=status,
                    position_id=position_id,
                    amount_y_atomic=amount_y,
                    network_cost_y_atomic=network_atomic,
                )
            )
        except Exception:
            items.append(
                MarketPaperExplorationItem(
                    pool_address=pool_address,
                    token_y_mint=token_y_mint,
                    decision_observed_at=decision_at,
                    status="PAPER_ENTRY_FAILED",
                    position_id=None,
                    amount_y_atomic=None,
                    network_cost_y_atomic=None,
                )
            )

    return MarketPaperExplorationReport(
        account_id=account_id,
        run_id=run_id,
        observed_at=observed_at,
        paper_only=True,
        policy_actionable=False,
        live_authorized=False,
        pools_ready=len(ready),
        pools_considered=considered,
        positions_opened=opened,
        max_new_positions=max_new_positions,
        items=tuple(items),
    )
