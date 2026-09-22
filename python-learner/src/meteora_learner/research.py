from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .backtest import evaluate_candidate_grid
from .candidates import generate_range_candidates
from .dlmm_math import price_ratio_to_bin_delta
from .research_store import ResearchStore
from .strategy import StrategyType


def inventory_backtest_from_store(
    database_path: str | Path,
    *,
    pool_address: str,
    capital_quote: float,
    candle_limit: int = 250,
    half_widths: Sequence[int] = (1, 2, 5, 10, 20, 30),
    center_offsets: Sequence[int] = (0,),
) -> list[dict[str, object]]:
    """
    Run inventory-only candidate comparisons over stored candles.

    This intentionally uses ZERO simulated fees. Its output is useful for range
    survival/inventory/IL research, not as a full profitability estimate.
    """
    store = ResearchStore(database_path)
    snapshot = store.latest_pool_snapshot(pool_address)
    if snapshot is None:
        raise ValueError(f"no pool snapshot found for {pool_address}")

    latest_price = snapshot.get("current_price")
    latest_active_id = snapshot.get("active_bin_id")
    bin_step = snapshot.get("bin_step")
    if latest_price is None or latest_active_id is None or bin_step is None:
        raise ValueError("pool snapshot is missing current_price, active_bin_id or bin_step")

    candles = store.load_ohlcv(pool_address, limit=candle_limit)
    if len(candles) < 2:
        raise ValueError("need at least two stored candles")

    prices = [float(row["close"]) for row in candles if row.get("close") is not None]
    if len(prices) < 2:
        raise ValueError("need at least two candles with close prices")

    entry_price = prices[0]
    entry_delta_from_latest = price_ratio_to_bin_delta(
        entry_price,
        float(latest_price),
        int(bin_step),
        round_down=True,
    )
    entry_active_id = int(latest_active_id) + entry_delta_from_latest

    candidates = generate_range_candidates(
        pool_address=pool_address,
        active_bin_id=entry_active_id,
        current_price=entry_price,
        bin_step=int(bin_step),
        capital_quote=capital_quote,
        half_widths=half_widths,
        center_offsets=center_offsets,
        strategies=(StrategyType.SPOT, StrategyType.CURVE, StrategyType.BID_ASK),
    )

    results = evaluate_candidate_grid(candidates, prices)
    return [item.to_record() for item in results]
