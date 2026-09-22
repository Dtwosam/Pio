from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

from .candidates import RangeCandidate
from .simulator import SimulationResult, create_position, simulate_price_path


@dataclass(frozen=True)
class CandidateBacktest:
    pool_address: str
    strategy: str
    min_bin_id: int
    max_bin_id: int
    width_bins: int
    lower_price: float
    upper_price: float
    capital_quote: float
    base_allocation_pct: float
    simulation: SimulationResult

    def to_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            "pool_address": self.pool_address,
            "strategy": self.strategy,
            "min_bin_id": self.min_bin_id,
            "max_bin_id": self.max_bin_id,
            "width_bins": self.width_bins,
            "lower_price": self.lower_price,
            "upper_price": self.upper_price,
            "capital_quote": self.capital_quote,
            "base_allocation_pct": self.base_allocation_pct,
        }
        record.update(asdict(self.simulation))
        return record


def evaluate_candidate(
    candidate: RangeCandidate,
    prices: Sequence[float],
    *,
    base_allocation_pct: float = 50.0,
    attributable_fee_quote_by_step: Sequence[float] | None = None,
    entry_cost_quote: float = 0.0,
    rebalance_cost_quote: float = 0.0,
    exit_cost_quote: float = 0.0,
) -> CandidateBacktest:
    if not 0.0 <= base_allocation_pct <= 100.0:
        raise ValueError("base_allocation_pct must be between 0 and 100")
    if not prices:
        raise ValueError("prices cannot be empty")

    base_quote = candidate.capital_quote * base_allocation_pct / 100.0
    quote_amount = candidate.capital_quote - base_quote
    amount_x = base_quote / candidate.current_price

    state = create_position(
        min_bin_id=candidate.min_bin_id,
        max_bin_id=candidate.max_bin_id,
        active_id=candidate.active_bin_id,
        bin_step=candidate.bin_step,
        amount_x=amount_x,
        amount_y=quote_amount,
        strategy=candidate.strategy,
        entry_price=candidate.current_price,
        entry_cost_quote=entry_cost_quote,
    )
    simulation = simulate_price_path(
        state,
        prices,
        attributable_fee_quote_by_step=attributable_fee_quote_by_step,
        rebalance_cost_quote=rebalance_cost_quote,
        exit_cost_quote=exit_cost_quote,
    )

    return CandidateBacktest(
        pool_address=candidate.pool_address,
        strategy=candidate.strategy.value,
        min_bin_id=candidate.min_bin_id,
        max_bin_id=candidate.max_bin_id,
        width_bins=candidate.width_bins,
        lower_price=candidate.lower_price,
        upper_price=candidate.upper_price,
        capital_quote=candidate.capital_quote,
        base_allocation_pct=base_allocation_pct,
        simulation=simulation,
    )


def evaluate_candidate_grid(
    candidates: Sequence[RangeCandidate],
    prices: Sequence[float],
    *,
    base_allocation_pct: float = 50.0,
    entry_cost_quote: float = 0.0,
    exit_cost_quote: float = 0.0,
) -> list[CandidateBacktest]:
    return [
        evaluate_candidate(
            candidate,
            prices,
            base_allocation_pct=base_allocation_pct,
            entry_cost_quote=entry_cost_quote,
            exit_cost_quote=exit_cost_quote,
        )
        for candidate in candidates
    ]
