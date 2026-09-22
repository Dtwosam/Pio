from __future__ import annotations

from dataclasses import dataclass
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

FEATURES = [
    "fee_yield_24h",
    "volatility_24h",
    "volume_acceleration",
    "liquidity_score",
    "estimated_cost_pct",
    "range_width_bins",
]
TARGET = "realized_net_return"


@dataclass
class TrainingResult:
    model: HistGradientBoostingRegressor
    train_rows: int
    validation_rows: int
    validation_mae: float


def train_walk_forward_frame(df: pd.DataFrame, split_fraction: float = 0.8) -> TrainingResult:
    """Time-ordered holdout. Input dataframe must already be sorted by decision timestamp."""
    missing = [c for c in FEATURES + [TARGET] if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    if len(df) < 50:
        raise ValueError("need at least 50 observations for the first training pass")

    cut = max(1, min(len(df) - 1, int(len(df) * split_fraction)))
    train = df.iloc[:cut]
    valid = df.iloc[cut:]

    model = HistGradientBoostingRegressor(random_state=42)
    model.fit(train[FEATURES], train[TARGET])
    pred = model.predict(valid[FEATURES])
    mae = float(mean_absolute_error(valid[TARGET], pred))

    return TrainingResult(
        model=model,
        train_rows=len(train),
        validation_rows=len(valid),
        validation_mae=mae,
    )
