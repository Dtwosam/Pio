from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class RangePathStats:
    candles: int
    close_in_range_ratio: float
    touched_range_ratio: float
    survived_all_closes: bool
    first_close_outside_at: str | None
    ending_return_pct: float
    max_adverse_pct: float
    max_favorable_pct: float


def evaluate_range_path(
    candles: pd.DataFrame,
    *,
    lower_price: float,
    upper_price: float,
    entry_price: float | None = None,
) -> RangePathStats:
    if lower_price <= 0 or upper_price <= 0 or lower_price >= upper_price:
        raise ValueError("range prices must be positive and lower_price < upper_price")
    if candles.empty:
        raise ValueError("candles cannot be empty")

    required = {"candle_time", "high", "low", "close"}
    missing = required - set(candles.columns)
    if missing:
        raise ValueError(f"missing candle columns: {sorted(missing)}")

    frame = candles.copy()
    frame["candle_time"] = pd.to_datetime(frame["candle_time"], utc=True)
    frame = frame.sort_values("candle_time").drop_duplicates("candle_time", keep="last")
    for column in ("high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["high", "low", "close"])

    if frame.empty:
        raise ValueError("candles contain no valid price rows")

    close = frame["close"]
    high = frame["high"]
    low = frame["low"]

    reference = float(entry_price if entry_price is not None else close.iloc[0])
    if reference <= 0:
        raise ValueError("entry_price must be positive")

    close_in = close.between(lower_price, upper_price, inclusive="both")
    touched = (high >= lower_price) & (low <= upper_price)
    outside = frame.loc[~close_in, "candle_time"]

    returns_pct = (close / reference - 1.0) * 100.0

    return RangePathStats(
        candles=len(frame),
        close_in_range_ratio=float(close_in.mean()),
        touched_range_ratio=float(touched.mean()),
        survived_all_closes=bool(close_in.all()),
        first_close_outside_at=None if outside.empty else outside.iloc[0].isoformat(),
        ending_return_pct=float(returns_pct.iloc[-1]),
        max_adverse_pct=float(returns_pct.min()),
        max_favorable_pct=float(returns_pct.max()),
    )
