from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from statistics import fmean
from typing import Any

from .liquidity_math import Q64
from .phase_promotion import PHASE8, PHASE8_EVIDENCE_TYPE
from .research_store import ResearchStore
from .storage import Storage


STATIC_HEDGE_EVIDENCE_TYPE = "PHASE9_STATIC_INVENTORY_HEDGE_V1"


@dataclass(frozen=True)
class HedgeInstrumentAssumptions:
    instrument_id: str
    venue: str
    available_liquidity_y_atomic: float
    max_liquidity_share_bps: int = 1_000
    max_leverage: float = 1.0
    funding_bps_per_holding_window: float = 0.0

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise ValueError("instrument_id is required")
        if not self.venue.strip():
            raise ValueError("venue is required")
        if self.available_liquidity_y_atomic <= 0:
            raise ValueError(
                "available_liquidity_y_atomic must be positive"
            )
        if not 1 <= self.max_liquidity_share_bps <= 10_000:
            raise ValueError(
                "max_liquidity_share_bps must be between 1 and 10000"
            )
        if self.max_leverage <= 0:
            raise ValueError("max_leverage must be positive")
        if self.funding_bps_per_holding_window < 0:
            raise ValueError(
                "funding_bps_per_holding_window cannot be negative"
            )


@dataclass(frozen=True)
class StaticHedgeCriteria:
    observation_limit: int = 96
    holding_observations: int = 6
    hedge_fraction: float = 1.0
    hedge_round_trip_cost_bps: float = 10.0
    min_windows: int = 20
    min_mean_abs_return_reduction_bps: float = 0.0
    min_worst_loss_improvement_bps: float = 0.0
    max_mean_return_drag_bps: float = 100.0

    def __post_init__(self) -> None:
        if self.observation_limit < 3:
            raise ValueError("observation_limit must be at least 3")
        if self.holding_observations < 1:
            raise ValueError("holding_observations must be positive")
        if not 0.0 <= self.hedge_fraction <= 1.0:
            raise ValueError("hedge_fraction must be between 0 and 1")
        if self.hedge_round_trip_cost_bps < 0:
            raise ValueError(
                "hedge_round_trip_cost_bps cannot be negative"
            )
        if self.min_windows < 1:
            raise ValueError("min_windows must be positive")
        if self.min_mean_abs_return_reduction_bps < 0:
            raise ValueError(
                "min_mean_abs_return_reduction_bps cannot be negative"
            )
        if self.min_worst_loss_improvement_bps < 0:
            raise ValueError(
                "min_worst_loss_improvement_bps cannot be negative"
            )
        if self.max_mean_return_drag_bps < 0:
            raise ValueError(
                "max_mean_return_drag_bps cannot be negative"
            )


@dataclass(frozen=True)
class StaticHedgeWindow:
    start_observed_at: str
    end_observed_at: str
    entry_price_q64: int
    exit_price_q64: int
    unhedged_return_bps: float
    hedged_return_bps: float
    hedge_notional_y_atomic: float
    hedge_liquidity_share_bps: int
    hedge_notional_to_lp_value_bps: int
    hedge_pnl_y_atomic: float
    hedge_cost_y_atomic: float


@dataclass(frozen=True)
class StaticHedgeResearchReport:
    pool_address: str
    as_of: str | None
    phase8_promoted: bool
    research_only: bool
    policy_actionable: bool
    status: str
    amount_x: int
    amount_y: int
    windows: int
    mean_unhedged_return_bps: float | None
    mean_hedged_return_bps: float | None
    mean_return_drag_bps: float | None
    mean_abs_unhedged_return_bps: float | None
    mean_abs_hedged_return_bps: float | None
    mean_abs_return_reduction_bps: float | None
    worst_unhedged_return_bps: float | None
    worst_hedged_return_bps: float | None
    worst_loss_improvement_bps: float | None
    max_hedge_liquidity_share_bps: int | None
    max_hedge_notional_to_lp_value_bps: int | None
    liquidity_constrained_windows: int
    leverage_constrained_windows: int
    instrument: HedgeInstrumentAssumptions
    criteria: StaticHedgeCriteria
    research_qualified: bool
    reasons: tuple[str, ...]
    window_results: tuple[StaticHedgeWindow, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _value_y_atomic(
    amount_x: int,
    amount_y: int,
    price_q64: int,
) -> Decimal:
    if price_q64 <= 0:
        raise ValueError("Q64 price must be positive")
    return Decimal(amount_y) + (
        Decimal(amount_x)
        * Decimal(price_q64)
        / Decimal(Q64)
    )


def _active_price_path(
    database_path: str,
    *,
    pool_address: str,
    observation_limit: int,
    as_of: str | None,
) -> list[tuple[str, int]]:
    store = ResearchStore(database_path)
    times = store.chain_observation_times(
        pool_address,
        limit=None,
        ascending=True,
    )
    if as_of is not None:
        # ISO timestamps collected by Pio are timezone-aware and canonical.
        # Parse-free filtering here mirrors the exact stored observation keys;
        # future leakage is independently covered by walk-forward tests.
        from datetime import datetime

        cutoff = datetime.fromisoformat(
            as_of.replace("Z", "+00:00")
        )
        times = [
            value
            for value in times
            if datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
            <= cutoff
        ]
    if observation_limit:
        times = times[-observation_limit:]

    output: list[tuple[str, int]] = []
    for observed_at in times:
        pool = store.chain_pool_snapshot_at(
            pool_address,
            observed_at,
        )
        if pool is None:
            continue
        active_bin_id = int(pool["active_bin_id"])
        row = store.bin_liquidity_at(
            pool_address,
            observed_at=observed_at,
            bin_id=active_bin_id,
        )
        if row is None:
            raise ValueError(
                f"active-bin price missing at {observed_at}"
            )
        price = int(str(row["price"]))
        if price <= 0:
            raise ValueError(
                f"active-bin price is non-positive at {observed_at}"
            )
        output.append((observed_at, price))
    return output


def research_static_inventory_hedge(
    storage: Storage,
    *,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    instrument: HedgeInstrumentAssumptions,
    criteria: StaticHedgeCriteria = StaticHedgeCriteria(),
    as_of: str | None = None,
) -> StaticHedgeResearchReport:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if amount_x < 0 or amount_y < 0:
        raise ValueError("token amounts cannot be negative")
    if amount_x == 0 and amount_y == 0:
        raise ValueError("at least one token amount must be positive")

    phase8_promoted = storage.phase_is_promoted(
        PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
    )
    path = _active_price_path(
        str(storage.path),
        pool_address=pool_address,
        observation_limit=criteria.observation_limit,
        as_of=as_of,
    )

    window_results: list[StaticHedgeWindow] = []
    horizon = criteria.holding_observations
    hedge_x = (
        Decimal(amount_x)
        * Decimal(str(criteria.hedge_fraction))
    )
    cost_rate = (
        Decimal(str(criteria.hedge_round_trip_cost_bps))
        / Decimal(10_000)
    )
    funding_rate = (
        Decimal(str(instrument.funding_bps_per_holding_window))
        / Decimal(10_000)
    )
    available_liquidity = Decimal(
        str(instrument.available_liquidity_y_atomic)
    )
    max_liquidity_share = Decimal(
        instrument.max_liquidity_share_bps
    ) / Decimal(10_000)
    max_leverage = Decimal(str(instrument.max_leverage))
    liquidity_constrained_windows = 0
    leverage_constrained_windows = 0

    for start in range(0, len(path) - horizon):
        start_time, entry_price = path[start]
        end_time, exit_price = path[start + horizon]
        initial_value = _value_y_atomic(
            amount_x,
            amount_y,
            entry_price,
        )
        if initial_value <= 0:
            continue
        end_value = _value_y_atomic(
            amount_x,
            amount_y,
            exit_price,
        )
        unhedged_pnl = end_value - initial_value

        entry_x_notional = (
            hedge_x
            * Decimal(entry_price)
            / Decimal(Q64)
        )
        hedge_gross_pnl = (
            hedge_x
            * Decimal(entry_price - exit_price)
            / Decimal(Q64)
        )
        hedge_cost = entry_x_notional * (cost_rate + funding_rate)
        liquidity_share = (
            entry_x_notional / available_liquidity
            if available_liquidity > 0
            else Decimal("Infinity")
        )
        notional_to_lp = entry_x_notional / initial_value
        if liquidity_share > max_liquidity_share:
            liquidity_constrained_windows += 1
        if notional_to_lp > max_leverage:
            leverage_constrained_windows += 1
        hedged_pnl = unhedged_pnl + hedge_gross_pnl - hedge_cost

        unhedged_return = float(
            unhedged_pnl
            * Decimal(10_000)
            / initial_value
        )
        hedged_return = float(
            hedged_pnl
            * Decimal(10_000)
            / initial_value
        )
        window_results.append(
            StaticHedgeWindow(
                start_observed_at=start_time,
                end_observed_at=end_time,
                entry_price_q64=entry_price,
                exit_price_q64=exit_price,
                unhedged_return_bps=unhedged_return,
                hedged_return_bps=hedged_return,
                hedge_notional_y_atomic=float(entry_x_notional),
                hedge_liquidity_share_bps=int(
                    liquidity_share * Decimal(10_000)
                ),
                hedge_notional_to_lp_value_bps=int(
                    notional_to_lp * Decimal(10_000)
                ),
                hedge_pnl_y_atomic=float(hedge_gross_pnl),
                hedge_cost_y_atomic=float(hedge_cost),
            )
        )

    if window_results:
        unhedged = [
            item.unhedged_return_bps
            for item in window_results
        ]
        hedged = [
            item.hedged_return_bps
            for item in window_results
        ]
        mean_unhedged = fmean(unhedged)
        mean_hedged = fmean(hedged)
        mean_drag = mean_unhedged - mean_hedged
        mean_abs_unhedged = fmean(abs(value) for value in unhedged)
        mean_abs_hedged = fmean(abs(value) for value in hedged)
        abs_reduction = mean_abs_unhedged - mean_abs_hedged
        worst_unhedged = min(unhedged)
        worst_hedged = min(hedged)
        worst_improvement = worst_hedged - worst_unhedged
        max_liquidity_share_bps = max(
            item.hedge_liquidity_share_bps
            for item in window_results
        )
        max_notional_to_lp_bps = max(
            item.hedge_notional_to_lp_value_bps
            for item in window_results
        )
    else:
        mean_unhedged = None
        mean_hedged = None
        mean_drag = None
        mean_abs_unhedged = None
        mean_abs_hedged = None
        abs_reduction = None
        worst_unhedged = None
        worst_hedged = None
        worst_improvement = None
        max_liquidity_share_bps = None
        max_notional_to_lp_bps = None

    reasons: list[str] = []
    checks = (
        (
            len(window_results) >= criteria.min_windows,
            f"hedge windows {len(window_results)} are below "
            f"{criteria.min_windows}",
        ),
        (
            abs_reduction is not None
            and abs_reduction
            >= criteria.min_mean_abs_return_reduction_bps,
            "mean absolute return reduction is below required minimum",
        ),
        (
            worst_improvement is not None
            and worst_improvement
            >= criteria.min_worst_loss_improvement_bps,
            "worst-loss improvement is below required minimum",
        ),
        (
            mean_drag is not None
            and mean_drag <= criteria.max_mean_return_drag_bps,
            "mean return drag exceeds configured maximum",
        ),
        (
            liquidity_constrained_windows == 0,
            "hedge notional exceeds configured liquidity-share cap",
        ),
        (
            leverage_constrained_windows == 0,
            "hedge notional exceeds configured leverage cap",
        ),
    )
    reasons.extend(message for passed, message in checks if not passed)
    if not phase8_promoted:
        reasons.insert(
            0,
            "Phase 8 must be persistently promoted before hedge research can qualify",
        )

    qualified = not reasons
    if not phase8_promoted:
        status = "RESEARCH_ONLY_PHASE8_BLOCKED"
    elif len(window_results) < criteria.min_windows:
        status = "INSUFFICIENT_HISTORY"
    elif qualified:
        status = "QUALIFIED_RESEARCH"
    else:
        status = "NOT_QUALIFIED"

    return StaticHedgeResearchReport(
        pool_address=pool_address,
        as_of=as_of,
        phase8_promoted=phase8_promoted,
        research_only=True,
        policy_actionable=False,
        status=status,
        amount_x=amount_x,
        amount_y=amount_y,
        windows=len(window_results),
        mean_unhedged_return_bps=mean_unhedged,
        mean_hedged_return_bps=mean_hedged,
        mean_return_drag_bps=mean_drag,
        mean_abs_unhedged_return_bps=mean_abs_unhedged,
        mean_abs_hedged_return_bps=mean_abs_hedged,
        mean_abs_return_reduction_bps=abs_reduction,
        worst_unhedged_return_bps=worst_unhedged,
        worst_hedged_return_bps=worst_hedged,
        worst_loss_improvement_bps=worst_improvement,
        max_hedge_liquidity_share_bps=max_liquidity_share_bps,
        max_hedge_notional_to_lp_value_bps=max_notional_to_lp_bps,
        liquidity_constrained_windows=liquidity_constrained_windows,
        leverage_constrained_windows=leverage_constrained_windows,
        instrument=instrument,
        criteria=criteria,
        research_qualified=qualified,
        reasons=tuple(reasons),
        window_results=tuple(window_results),
    )


def persist_static_hedge_research(
    storage: Storage,
    *,
    report: StaticHedgeResearchReport,
) -> int:
    return storage.save_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address=report.pool_address,
        as_of=report.as_of,
        status=report.status,
        qualified=report.research_qualified,
        evidence=report.to_record(),
    )
