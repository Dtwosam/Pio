from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


REQUIRED_POOL_MARKET_COLUMNS = {
    "pool_address",
    "observed_at",
    "price",
    "tvl_usd",
    "volume_24h_usd",
    "fees_24h_usd",
}

POOL_MARKET_FEATURE_COLUMNS = (
    "price",
    "tvl_usd",
    "volume_24h_usd",
    "fees_24h_usd",
    "price_return_1",
    "realized_volatility",
    "price_drawdown",
    "volume_change_1",
    "volume_acceleration",
    "tvl_change_1",
    "volume_to_tvl",
    "fees_to_tvl",
    "fees_to_volume",
    "history_observations",
)

POOL_MARKET_TARGET_COLUMNS = (
    "target_price_return",
    "target_tvl_return",
    "target_max_price_drawdown",
    "target_max_tvl_drawdown",
    "target_end_volume_to_tvl",
    "target_end_fees_to_tvl",
)


@dataclass(frozen=True)
class PoolMarketLearningExample:
    pool_address: str
    decision_observed_at: str
    forward_end_observed_at: str

    price: float
    tvl_usd: float
    volume_24h_usd: float
    fees_24h_usd: float
    price_return_1: float
    realized_volatility: float
    price_drawdown: float
    volume_change_1: float
    volume_acceleration: float
    tvl_change_1: float
    volume_to_tvl: float
    fees_to_tvl: float
    fees_to_volume: float
    history_observations: int

    target_price_return: float
    target_tvl_return: float
    target_max_price_drawdown: float
    target_max_tvl_drawdown: float
    target_end_volume_to_tvl: float
    target_end_fees_to_tvl: float


@dataclass(frozen=True)
class PoolMarketLearningDataset:
    rows_seen: int
    pools_seen: int
    examples_built: int
    rows_dropped: int
    drop_reasons: tuple[tuple[str, int], ...]
    examples: tuple[PoolMarketLearningExample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(asdict(item) for item in self.examples)


def _bump(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def _require_finite(row: pd.Series, columns: tuple[str, ...]) -> bool:
    return all(np.isfinite(float(row[column])) for column in columns)


def _prepare_history(
    history: pd.DataFrame,
    *,
    volatility_window: int,
    drawdown_window: int,
    activity_window: int,
) -> pd.DataFrame:
    missing = REQUIRED_POOL_MARKET_COLUMNS - set(history.columns)
    if missing:
        raise ValueError(
            f"missing pool-market columns: {sorted(missing)}"
        )

    frame = history.copy()
    frame["pool_address"] = frame["pool_address"].astype(str).str.strip()
    if (frame["pool_address"] == "").any():
        raise ValueError("pool_address cannot be empty")

    frame["observed_at"] = pd.to_datetime(
        frame["observed_at"],
        utc=True,
        errors="raise",
    )

    numeric = ("price", "tvl_usd", "volume_24h_usd", "fees_24h_usd")
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise")

    if (frame["price"] <= 0).any():
        raise ValueError("price must be positive")
    if (frame["tvl_usd"] <= 0).any():
        raise ValueError("tvl_usd must be positive")
    if (frame["volume_24h_usd"] < 0).any():
        raise ValueError("volume_24h_usd cannot be negative")
    if (frame["fees_24h_usd"] < 0).any():
        raise ValueError("fees_24h_usd cannot be negative")

    frame = (
        frame.sort_values(["pool_address", "observed_at"])
        .drop_duplicates(
            ["pool_address", "observed_at"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    pieces: list[pd.DataFrame] = []
    for _, group in frame.groupby("pool_address", sort=False):
        group = group.copy().reset_index(drop=True)

        price = group["price"].astype(float)
        tvl = group["tvl_usd"].astype(float)
        volume = group["volume_24h_usd"].astype(float)
        fees = group["fees_24h_usd"].astype(float)

        log_return = np.log(price).diff()

        group["price_return_1"] = price.pct_change()
        group["realized_volatility"] = log_return.rolling(
            volatility_window,
            min_periods=min(3, volatility_window),
        ).std()
        group["price_drawdown"] = (
            price
            / price.rolling(
                drawdown_window,
                min_periods=1,
            ).max()
            - 1.0
        )
        group["volume_change_1"] = volume.pct_change()
        volume_mean = volume.rolling(
            activity_window,
            min_periods=1,
        ).mean()
        group["volume_acceleration"] = (
            volume / volume_mean.replace(0, np.nan) - 1.0
        )
        group["tvl_change_1"] = tvl.pct_change()
        group["volume_to_tvl"] = volume / tvl
        group["fees_to_tvl"] = fees / tvl
        group["fees_to_volume"] = fees / volume.replace(0, np.nan)
        group["history_observations"] = np.arange(1, len(group) + 1)

        pieces.append(group)

    return pd.concat(pieces, ignore_index=True).replace(
        [np.inf, -np.inf],
        np.nan,
    )


def build_pool_market_learning_dataset(
    history: pd.DataFrame,
    *,
    horizon_rows: int = 6,
    volatility_window: int = 6,
    drawdown_window: int = 12,
    activity_window: int = 6,
) -> PoolMarketLearningDataset:
    """
    Build no-lookahead market-learning examples for cross-pool research.

    Decision-time features are calculated only from the selected pool's
    observation history at or before decision_observed_at. Later observations
    are used only to construct continuous forward targets.

    This builder deliberately does not create a hand-written economic risk
    score, a binary safe/unsafe label, or fixed volatility/drawdown cutoffs.
    Those economic relationships are intended to be learned and validated from
    observed outcomes. Validation here is limited to data integrity.
    """
    if history.empty:
        raise ValueError("pool-market history cannot be empty")
    if horizon_rows < 1:
        raise ValueError("horizon_rows must be positive")
    for name, value in (
        ("volatility_window", volatility_window),
        ("drawdown_window", drawdown_window),
        ("activity_window", activity_window),
    ):
        if value < 1:
            raise ValueError(f"{name} must be positive")

    frame = _prepare_history(
        history,
        volatility_window=volatility_window,
        drawdown_window=drawdown_window,
        activity_window=activity_window,
    )

    examples: list[PoolMarketLearningExample] = []
    drops: dict[str, int] = {}

    for pool_address, group in frame.groupby("pool_address", sort=False):
        group = group.reset_index(drop=True)

        for index in range(len(group)):
            row = group.iloc[index]

            if index + horizon_rows >= len(group):
                _bump(drops, "forward_horizon_incomplete")
                continue

            if not _require_finite(row, POOL_MARKET_FEATURE_COLUMNS[:-1]):
                _bump(drops, "decision_features_incomplete")
                continue

            future = group.iloc[index + 1 : index + horizon_rows + 1]
            end = future.iloc[-1]

            current_price = float(row["price"])
            current_tvl = float(row["tvl_usd"])
            end_price = float(end["price"])
            end_tvl = float(end["tvl_usd"])

            target_max_price_drawdown = (
                float(future["price"].min()) / current_price - 1.0
            )
            target_max_tvl_drawdown = (
                float(future["tvl_usd"].min()) / current_tvl - 1.0
            )
            target_end_volume_to_tvl = (
                float(end["volume_24h_usd"]) / end_tvl
            )
            target_end_fees_to_tvl = (
                float(end["fees_24h_usd"]) / end_tvl
            )

            target_values = (
                end_price / current_price - 1.0,
                end_tvl / current_tvl - 1.0,
                target_max_price_drawdown,
                target_max_tvl_drawdown,
                target_end_volume_to_tvl,
                target_end_fees_to_tvl,
            )
            if not all(np.isfinite(value) for value in target_values):
                _bump(drops, "forward_targets_incomplete")
                continue

            examples.append(
                PoolMarketLearningExample(
                    pool_address=str(pool_address),
                    decision_observed_at=row["observed_at"].isoformat(),
                    forward_end_observed_at=end["observed_at"].isoformat(),
                    price=current_price,
                    tvl_usd=current_tvl,
                    volume_24h_usd=float(row["volume_24h_usd"]),
                    fees_24h_usd=float(row["fees_24h_usd"]),
                    price_return_1=float(row["price_return_1"]),
                    realized_volatility=float(
                        row["realized_volatility"]
                    ),
                    price_drawdown=float(row["price_drawdown"]),
                    volume_change_1=float(row["volume_change_1"]),
                    volume_acceleration=float(
                        row["volume_acceleration"]
                    ),
                    tvl_change_1=float(row["tvl_change_1"]),
                    volume_to_tvl=float(row["volume_to_tvl"]),
                    fees_to_tvl=float(row["fees_to_tvl"]),
                    fees_to_volume=float(row["fees_to_volume"]),
                    history_observations=int(
                        row["history_observations"]
                    ),
                    target_price_return=target_values[0],
                    target_tvl_return=target_values[1],
                    target_max_price_drawdown=target_values[2],
                    target_max_tvl_drawdown=target_values[3],
                    target_end_volume_to_tvl=target_values[4],
                    target_end_fees_to_tvl=target_values[5],
                )
            )

    return PoolMarketLearningDataset(
        rows_seen=len(frame),
        pools_seen=int(frame["pool_address"].nunique()),
        examples_built=len(examples),
        rows_dropped=len(frame) - len(examples),
        drop_reasons=tuple(sorted(drops.items())),
        examples=tuple(examples),
    )
