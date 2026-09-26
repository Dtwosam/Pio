from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pandas as pd

from .chain_features import (
    fee_checkpoint_activity,
    summarize_liquidity_shape,
)
from .research_store import ResearchStore


POOL_CHAIN_CONTEXT_FEATURE_COLUMNS = (
    "chain_snapshot_age_seconds",
    "chain_observation_count",
    "chain_has_previous_snapshot",
    "chain_seconds_since_previous_snapshot",
    "chain_active_bin_id",
    "chain_active_bin_change",
    "chain_bin_step",
    "chain_total_fee_rate_raw",
    "chain_deposit_total_fee_rate_raw",
    "chain_protocol_share_bps",
    "chain_supports_limit_order",
    "chain_occupied_bins",
    "chain_total_liquidity_log10",
    "chain_active_liquidity_log10",
    "chain_near_active_liquidity_ratio",
    "chain_below_active_liquidity_ratio",
    "chain_above_active_liquidity_ratio",
    "chain_liquidity_weighted_distance_bins",
    "chain_total_liquidity_log10_change",
    "chain_active_liquidity_log10_change",
    "chain_near_active_liquidity_ratio_change",
    "chain_liquidity_weighted_distance_change",
    "chain_fee_checkpoint_matched_bins",
    "chain_fee_checkpoint_x_growth_bins",
    "chain_fee_checkpoint_y_growth_bins",
    "chain_fee_checkpoint_x_growth_log10",
    "chain_fee_checkpoint_y_growth_log10",
)


@dataclass(frozen=True)
class PoolChainContextReport:
    rows_seen: int
    rows_with_chain_context: int
    rows_with_previous_chain_snapshot: int
    rows_missing_chain_context: int
    rows_missing_bin_context: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: Any) -> pd.Timestamp:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid timestamp: {value}")
    return pd.Timestamp(parsed)


def _log10_nonnegative(value: int) -> float:
    if value < 0:
        raise ValueError("chain liquidity/activity value cannot be negative")
    return math.log10(1.0 + value)


def _empty_features() -> dict[str, float]:
    return {
        column: np.nan
        for column in POOL_CHAIN_CONTEXT_FEATURE_COLUMNS
    }


def attach_pool_chain_context_from_store(
    database_path: str,
    decision_frame: pd.DataFrame,
    *,
    near_radius: int = 5,
) -> tuple[pd.DataFrame, PoolChainContextReport]:
    """
    Attach as-of on-chain pool/liquidity history to decision rows.

    Fee-checkpoint growth is represented only as raw activity features. It is
    not treated as LP fee income. Future chain refreshes are excluded by
    filtering observation timestamps to the decision time before selecting the
    current and previous snapshots.
    """
    if near_radius < 0:
        raise ValueError("near_radius cannot be negative")

    required = {"pool_address", "decision_observed_at"}
    missing = sorted(required - set(decision_frame.columns))
    if missing:
        raise ValueError(
            f"missing chain-context decision columns: {missing}"
        )

    frame = decision_frame.copy()
    frame["pool_address"] = frame["pool_address"].astype(str).str.strip()
    if (frame["pool_address"] == "").any():
        raise ValueError("pool_address cannot be empty")
    frame["decision_observed_at"] = pd.to_datetime(
        frame["decision_observed_at"],
        utc=True,
        errors="coerce",
    )
    if frame["decision_observed_at"].isna().any():
        raise ValueError(
            "decision_observed_at contains invalid timestamps"
        )

    store = ResearchStore(database_path)
    all_times_by_pool: dict[str, list[str]] = {
        pool: store.chain_observation_times(
            pool,
            limit=None,
            ascending=True,
        )
        for pool in sorted(frame["pool_address"].unique())
    }

    feature_rows: list[dict[str, float]] = []
    with_context = 0
    with_previous = 0
    missing_chain = 0
    missing_bins = 0

    for row in frame.itertuples(index=False):
        pool = str(getattr(row, "pool_address"))
        decision_time = pd.Timestamp(
            getattr(row, "decision_observed_at")
        )

        eligible_times = [
            observed_at
            for observed_at in all_times_by_pool.get(pool, [])
            if _parse_time(observed_at) <= decision_time
        ]
        if not eligible_times:
            missing_chain += 1
            feature_rows.append(_empty_features())
            continue

        current_time_raw = eligible_times[-1]
        current_time = _parse_time(current_time_raw)
        previous_time_raw = (
            eligible_times[-2] if len(eligible_times) >= 2 else None
        )
        previous_time = (
            _parse_time(previous_time_raw)
            if previous_time_raw is not None
            else current_time
        )

        current_pool = store.chain_pool_snapshot_at(
            pool,
            current_time_raw,
        )
        if current_pool is None:
            raise ValueError(
                "chain observation time has no matching pool snapshot"
            )
        current_bins = store.load_bin_liquidity(
            pool,
            observed_at=current_time_raw,
        )
        if not current_bins:
            missing_bins += 1
            feature_rows.append(_empty_features())
            continue

        current_shape = summarize_liquidity_shape(
            current_bins,
            active_bin_id=int(current_pool["active_bin_id"]),
            near_radius=near_radius,
        )

        has_previous = previous_time_raw is not None
        if has_previous:
            previous_pool = store.chain_pool_snapshot_at(
                pool,
                previous_time_raw,
            )
            previous_bins = store.load_bin_liquidity(
                pool,
                observed_at=previous_time_raw,
            )
            if previous_pool is None or not previous_bins:
                raise ValueError(
                    "previous chain observation is incomplete"
                )
            previous_shape = summarize_liquidity_shape(
                previous_bins,
                active_bin_id=int(previous_pool["active_bin_id"]),
                near_radius=near_radius,
            )
            fee_activity = fee_checkpoint_activity(
                previous_bins,
                current_bins,
            )
            with_previous += 1
        else:
            previous_pool = current_pool
            previous_shape = current_shape
            fee_activity = fee_checkpoint_activity(
                current_bins,
                current_bins,
            )

        with_context += 1
        feature_rows.append(
            {
                "chain_snapshot_age_seconds": float(
                    (decision_time - current_time).total_seconds()
                ),
                "chain_observation_count": float(
                    len(eligible_times)
                ),
                "chain_has_previous_snapshot": float(has_previous),
                "chain_seconds_since_previous_snapshot": (
                    float(
                        (
                            current_time - previous_time
                        ).total_seconds()
                    )
                    if has_previous
                    else 0.0
                ),
                "chain_active_bin_id": float(
                    current_shape.active_bin_id
                ),
                "chain_active_bin_change": float(
                    current_shape.active_bin_id
                    - previous_shape.active_bin_id
                ),
                "chain_bin_step": float(current_pool["bin_step"]),
                "chain_total_fee_rate_raw": float(
                    int(str(current_pool["total_fee_rate"]))
                ),
                "chain_deposit_total_fee_rate_raw": float(
                    int(
                        str(
                            current_pool[
                                "deposit_total_fee_rate"
                            ]
                        )
                    )
                ),
                "chain_protocol_share_bps": float(
                    int(current_pool["protocol_share_bps"])
                ),
                "chain_supports_limit_order": float(
                    bool(current_pool["supports_limit_order"])
                ),
                "chain_occupied_bins": float(
                    current_shape.occupied_bins
                ),
                "chain_total_liquidity_log10": _log10_nonnegative(
                    current_shape.total_liquidity_supply
                ),
                "chain_active_liquidity_log10": _log10_nonnegative(
                    current_shape.active_liquidity_supply
                ),
                "chain_near_active_liquidity_ratio": (
                    current_shape.near_active_liquidity_ratio
                ),
                "chain_below_active_liquidity_ratio": (
                    current_shape.below_active_liquidity_ratio
                ),
                "chain_above_active_liquidity_ratio": (
                    current_shape.above_active_liquidity_ratio
                ),
                "chain_liquidity_weighted_distance_bins": (
                    current_shape.liquidity_weighted_distance_bins
                ),
                "chain_total_liquidity_log10_change": (
                    _log10_nonnegative(
                        current_shape.total_liquidity_supply
                    )
                    - _log10_nonnegative(
                        previous_shape.total_liquidity_supply
                    )
                ),
                "chain_active_liquidity_log10_change": (
                    _log10_nonnegative(
                        current_shape.active_liquidity_supply
                    )
                    - _log10_nonnegative(
                        previous_shape.active_liquidity_supply
                    )
                ),
                "chain_near_active_liquidity_ratio_change": (
                    current_shape.near_active_liquidity_ratio
                    - previous_shape.near_active_liquidity_ratio
                ),
                "chain_liquidity_weighted_distance_change": (
                    current_shape.liquidity_weighted_distance_bins
                    - previous_shape.liquidity_weighted_distance_bins
                ),
                "chain_fee_checkpoint_matched_bins": float(
                    fee_activity.matched_bins
                ),
                "chain_fee_checkpoint_x_growth_bins": float(
                    fee_activity.bins_with_x_growth
                ),
                "chain_fee_checkpoint_y_growth_bins": float(
                    fee_activity.bins_with_y_growth
                ),
                "chain_fee_checkpoint_x_growth_log10": (
                    _log10_nonnegative(
                        fee_activity.x_growth_raw
                    )
                ),
                "chain_fee_checkpoint_y_growth_log10": (
                    _log10_nonnegative(
                        fee_activity.y_growth_raw
                    )
                ),
            }
        )

    features = pd.DataFrame(feature_rows, index=frame.index)
    enriched = pd.concat([frame, features], axis=1)
    return enriched, PoolChainContextReport(
        rows_seen=len(frame),
        rows_with_chain_context=with_context,
        rows_with_previous_chain_snapshot=with_previous,
        rows_missing_chain_context=missing_chain,
        rows_missing_bin_context=missing_bins,
    )
