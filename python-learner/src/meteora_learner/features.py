from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = {"candle_time", "open", "high", "low", "close", "volume"}


def build_market_features(candles: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_COLUMNS - set(candles.columns)
    if missing:
        raise ValueError(f"missing candle columns: {sorted(missing)}")

    frame = candles.copy()
    frame["candle_time"] = pd.to_datetime(frame["candle_time"], utc=True)
    frame = frame.sort_values("candle_time").drop_duplicates("candle_time", keep="last")
    frame = frame.reset_index(drop=True)

    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")

    frame["log_return_1"] = np.log(close).diff()
    frame["momentum_3"] = close.pct_change(3)
    frame["momentum_6"] = close.pct_change(6)
    frame["realized_volatility_6"] = frame["log_return_1"].rolling(6, min_periods=3).std()
    frame["range_width_pct"] = (high - low) / close.replace(0, np.nan)
    frame["volume_change_1"] = volume.pct_change()
    frame["volume_ma_6"] = volume.rolling(6, min_periods=1).mean()
    frame["volume_acceleration"] = volume / frame["volume_ma_6"].replace(0, np.nan) - 1.0
    frame["drawdown_12"] = close / close.rolling(12, min_periods=1).max() - 1.0

    return frame.replace([np.inf, -np.inf], np.nan)
