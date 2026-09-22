from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .dlmm_math import relative_bin_price
from .strategy import StrategyType


@dataclass(frozen=True)
class RangeCandidate:
    pool_address: str
    strategy: StrategyType
    active_bin_id: int
    bin_step: int
    min_bin_id: int
    max_bin_id: int
    width_bins: int
    lower_price: float
    upper_price: float
    current_price: float
    capital_quote: float

    @property
    def lower_buffer_pct(self) -> float:
        return (self.current_price - self.lower_price) / self.current_price * 100.0

    @property
    def upper_buffer_pct(self) -> float:
        return (self.upper_price - self.current_price) / self.current_price * 100.0

    @property
    def narrowest_buffer_pct(self) -> float:
        return min(self.lower_buffer_pct, self.upper_buffer_pct)


def generate_range_candidates(
    *,
    pool_address: str,
    active_bin_id: int,
    current_price: float,
    bin_step: int,
    capital_quote: float,
    half_widths: Sequence[int] = (1, 2, 5, 10, 20, 30),
    strategies: Iterable[StrategyType | str] = (
        StrategyType.SPOT,
        StrategyType.CURVE,
        StrategyType.BID_ASK,
    ),
    center_offsets: Sequence[int] = (0,),
) -> list[RangeCandidate]:
    if not pool_address:
        raise ValueError("pool_address is required")
    if current_price <= 0:
        raise ValueError("current_price must be positive")
    if bin_step <= 0:
        raise ValueError("bin_step must be positive")
    if capital_quote <= 0:
        raise ValueError("capital_quote must be positive")
    if not half_widths:
        raise ValueError("at least one half width is required")

    normalized_strategies = [StrategyType(strategy) for strategy in strategies]
    out: list[RangeCandidate] = []
    seen: set[tuple[str, int, int]] = set()

    for half_width in half_widths:
        if half_width < 0:
            raise ValueError("half widths cannot be negative")

        for offset in center_offsets:
            center = active_bin_id + int(offset)
            min_bin_id = center - half_width
            max_bin_id = center + half_width
            lower_price = float(
                relative_bin_price(current_price, min_bin_id - active_bin_id, bin_step)
            )
            upper_price = float(
                relative_bin_price(current_price, max_bin_id - active_bin_id, bin_step)
            )

            for strategy in normalized_strategies:
                key = (strategy.value, min_bin_id, max_bin_id)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    RangeCandidate(
                        pool_address=pool_address,
                        strategy=strategy,
                        active_bin_id=active_bin_id,
                        bin_step=bin_step,
                        min_bin_id=min_bin_id,
                        max_bin_id=max_bin_id,
                        width_bins=max_bin_id - min_bin_id + 1,
                        lower_price=lower_price,
                        upper_price=upper_price,
                        current_price=current_price,
                        capital_quote=capital_quote,
                    )
                )

    return out
