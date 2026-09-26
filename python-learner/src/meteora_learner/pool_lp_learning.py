from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from .pool_market_learning import (
    POOL_MARKET_FEATURE_COLUMNS,
    build_pool_market_feature_history,
    load_pool_market_history,
)


MARKET_CONTEXT_FEATURE_COLUMNS = tuple(
    f"market_{column}"
    for column in POOL_MARKET_FEATURE_COLUMNS
) + ("market_snapshot_age_seconds",)


@dataclass(frozen=True)
class PoolLPMarketEnrichmentReport:
    lp_rows_seen: int
    market_rows_seen: int
    rows_matched: int
    rows_unmatched: int
    rows_with_incomplete_market_features: int
    pools_in_lp_rows: int
    pools_in_market_history: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_lp_frame(lp_frame: pd.DataFrame) -> pd.DataFrame:
    required = {"pool_address", "decision_observed_at"}
    missing = sorted(required - set(lp_frame.columns))
    if missing:
        raise ValueError(
            f"missing LP example columns: {missing}"
        )

    work = lp_frame.copy()
    work["pool_address"] = (
        work["pool_address"].astype(str).str.strip()
    )
    if (work["pool_address"] == "").any():
        raise ValueError("LP pool_address cannot be empty")

    work["decision_observed_at"] = pd.to_datetime(
        work["decision_observed_at"],
        utc=True,
        errors="coerce",
    )
    if work["decision_observed_at"].isna().any():
        raise ValueError(
            "LP decision_observed_at contains invalid timestamps"
        )

    work["_lp_original_order"] = np.arange(len(work))
    return work


def enrich_lp_examples_with_market_state(
    lp_frame: pd.DataFrame,
    market_history: pd.DataFrame,
    *,
    volatility_window: int = 6,
    drawdown_window: int = 12,
    activity_window: int = 6,
) -> tuple[pd.DataFrame, PoolLPMarketEnrichmentReport]:
    """
    Attach the latest available decision-time market state to LP examples.

    Matching is per pool and strictly backward in time. A market snapshot later
    than an LP decision can never be attached to that decision. Unmatched and
    incomplete rows are retained and reported rather than synthesized.
    """
    lp = _normalize_lp_frame(lp_frame)
    if lp.empty:
        report = PoolLPMarketEnrichmentReport(
            lp_rows_seen=0,
            market_rows_seen=len(market_history),
            rows_matched=0,
            rows_unmatched=0,
            rows_with_incomplete_market_features=0,
            pools_in_lp_rows=0,
            pools_in_market_history=0,
        )
        return lp.drop(columns=["_lp_original_order"]), report

    market = build_pool_market_feature_history(
        market_history,
        volatility_window=volatility_window,
        drawdown_window=drawdown_window,
        activity_window=activity_window,
    ).copy()

    market["pool_address"] = (
        market["pool_address"].astype(str).str.strip()
    )
    market["observed_at"] = pd.to_datetime(
        market["observed_at"],
        utc=True,
        errors="coerce",
    )
    if market["observed_at"].isna().any():
        raise ValueError(
            "market observed_at contains invalid timestamps"
        )

    pieces: list[pd.DataFrame] = []

    for pool_address, lp_group in lp.groupby(
        "pool_address",
        sort=False,
    ):
        left = lp_group.sort_values(
            "decision_observed_at"
        ).copy()
        right = market[
            market["pool_address"] == pool_address
        ].sort_values("observed_at").copy()

        if right.empty:
            merged = left.copy()
            merged["market_observed_at"] = pd.NaT
            for column in POOL_MARKET_FEATURE_COLUMNS:
                merged[f"market_{column}"] = np.nan
            merged["market_snapshot_age_seconds"] = np.nan
            pieces.append(merged)
            continue

        right_columns = [
            "observed_at",
            *POOL_MARKET_FEATURE_COLUMNS,
        ]
        right = right[right_columns].rename(
            columns={
                "observed_at": "market_observed_at",
                **{
                    column: f"market_{column}"
                    for column in POOL_MARKET_FEATURE_COLUMNS
                },
            }
        )

        merged = pd.merge_asof(
            left,
            right,
            left_on="decision_observed_at",
            right_on="market_observed_at",
            direction="backward",
            allow_exact_matches=True,
        )
        age = (
            merged["decision_observed_at"]
            - merged["market_observed_at"]
        ).dt.total_seconds()
        merged["market_snapshot_age_seconds"] = age
        pieces.append(merged)

    enriched = pd.concat(
        pieces,
        ignore_index=True,
        sort=False,
    )
    enriched = enriched.sort_values(
        "_lp_original_order"
    ).reset_index(drop=True)

    matched = enriched["market_observed_at"].notna()
    if (
        enriched.loc[
            matched,
            "market_snapshot_age_seconds",
        ]
        < 0
    ).any():
        raise AssertionError(
            "backward market join produced a future observation"
        )

    market_feature_columns = [
        f"market_{column}"
        for column in POOL_MARKET_FEATURE_COLUMNS
    ]
    incomplete = (
        matched
        & enriched[market_feature_columns]
        .isna()
        .any(axis=1)
    )

    report = PoolLPMarketEnrichmentReport(
        lp_rows_seen=len(lp),
        market_rows_seen=len(market),
        rows_matched=int(matched.sum()),
        rows_unmatched=int((~matched).sum()),
        rows_with_incomplete_market_features=int(
            incomplete.sum()
        ),
        pools_in_lp_rows=int(
            lp["pool_address"].nunique()
        ),
        pools_in_market_history=int(
            market["pool_address"].nunique()
        ),
    )

    enriched = enriched.drop(
        columns=["_lp_original_order"]
    )
    return enriched, report


def enrich_lp_examples_from_store(
    database_path: str,
    lp_frame: pd.DataFrame,
    *,
    pool_addresses: tuple[str, ...] | None = None,
    start_observed_at: str | None = None,
    end_observed_at: str | None = None,
    volatility_window: int = 6,
    drawdown_window: int = 12,
    activity_window: int = 6,
) -> tuple[pd.DataFrame, PoolLPMarketEnrichmentReport]:
    history = load_pool_market_history(
        database_path,
        pool_addresses=pool_addresses,
        start_observed_at=start_observed_at,
        end_observed_at=end_observed_at,
    )
    return enrich_lp_examples_with_market_state(
        lp_frame,
        history,
        volatility_window=volatility_window,
        drawdown_window=drawdown_window,
        activity_window=activity_window,
    )
