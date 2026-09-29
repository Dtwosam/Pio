from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

import pandas as pd

from .baseline_policy import (
    BaselinePolicyConfig,
    replay_economics,
    select_deterministic_baseline,
)
from .chain_features import fee_checkpoint_activity, summarize_liquidity_shape
from .chain_scan import scan_chain_candidates
from .composition_fee import FEE_PRECISION
from .ml_dataset import ML_FEATURE_COLUMNS
from .research_store import ResearchStore
from .strategy import StrategyType


CURRENT_CANDIDATE_COLUMNS = (
    "pool_address",
    "decision_observed_at",
    "strategy",
    "baseline_selected",
    *ML_FEATURE_COLUMNS,
)


@dataclass(frozen=True)
class MLCurrentCandidateFrameReport:
    pool_address: str
    decision_observed_at: str
    previous_observed_at: str
    lookback_observations: int
    candidates_seen: int
    candidates_built: int
    candidates_dropped: int
    drop_reasons: tuple[tuple[str, int], ...]
    rows: tuple[dict[str, Any], ...]
    no_lookahead: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=CURRENT_CANDIDATE_COLUMNS)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("current ML candidate timestamps require timezone")
    return parsed.astimezone(timezone.utc)


def _decision_window(
    store: ResearchStore,
    *,
    pool_address: str,
    lookback_observations: int,
    as_of: str | None,
) -> tuple[list[str], str, str]:
    if lookback_observations < 2:
        raise ValueError("lookback_observations must be at least 2")
    times = store.chain_observation_times(
        pool_address,
        limit=None,
        ascending=True,
    )
    if as_of is not None:
        cutoff = _parse_time(as_of)
        times = [value for value in times if _parse_time(value) <= cutoff]
    if len(times) < lookback_observations:
        raise ValueError(
            "not enough chain observations for current ML candidate frame"
        )
    selected = times[-lookback_observations:]
    return selected, selected[-2], selected[-1]


def _active_price(
    store: ResearchStore,
    *,
    pool_address: str,
    observed_at: str,
    active_bin_id: int,
) -> int:
    row = store.bin_liquidity_at(
        pool_address,
        observed_at=observed_at,
        bin_id=active_bin_id,
    )
    if row is None:
        raise ValueError("current ML candidate active-bin price is missing")
    return int(str(row["price"]))


def _bump(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def build_current_ml_candidate_frame(
    database_path: str,
    *,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    network_cost_y_atomic: int,
    lookback_observations: int = 12,
    half_widths: Sequence[int] = (0, 1, 2, 5, 10),
    center_offsets: Sequence[int] = (0,),
    strategies: Sequence[StrategyType | str] = (
        StrategyType.SPOT,
        StrategyType.CURVE,
        StrategyType.BID_ASK,
    ),
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
    near_liquidity_radius: int = 5,
    as_of: str | None = None,
) -> MLCurrentCandidateFrameReport:
    """
    Build current candidate inference features from trailing state only.

    No forward observation or target label is read. The feature definitions
    match ML_ACTION_FEATURES_V1 and use the same trailing candidate economics
    as the supervised action dataset.
    """
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if amount_x < 0 or amount_y < 0 or (amount_x == 0 and amount_y == 0):
        raise ValueError(
            "at least one non-negative token amount must be positive"
        )
    if network_cost_y_atomic < 0:
        raise ValueError("network_cost_y_atomic cannot be negative")
    if near_liquidity_radius < 0:
        raise ValueError("near_liquidity_radius cannot be negative")
    if max_share_bps <= 0:
        raise ValueError("max_share_bps must be positive")

    strategy_values = tuple(StrategyType(value) for value in strategies)
    if not strategy_values:
        raise ValueError("at least one strategy is required")

    store = ResearchStore(database_path)
    times, previous_time, decision_time = _decision_window(
        store,
        pool_address=pool_address,
        lookback_observations=lookback_observations,
        as_of=as_of,
    )

    current_pool = store.chain_pool_snapshot_at(
        pool_address,
        decision_time,
    )
    previous_pool = store.chain_pool_snapshot_at(
        pool_address,
        previous_time,
    )
    current_bins = store.load_bin_liquidity(
        pool_address,
        observed_at=decision_time,
    )
    previous_bins = store.load_bin_liquidity(
        pool_address,
        observed_at=previous_time,
    )
    if (
        current_pool is None
        or previous_pool is None
        or not current_bins
        or not previous_bins
    ):
        raise ValueError("current ML candidate decision state is incomplete")

    active_id = int(current_pool["active_bin_id"])
    previous_active_id = int(previous_pool["active_bin_id"])
    shape = summarize_liquidity_shape(
        current_bins,
        active_bin_id=active_id,
        near_radius=near_liquidity_radius,
    )
    activity = fee_checkpoint_activity(previous_bins, current_bins)
    total_liquidity = shape.total_liquidity_supply
    active_ratio = (
        shape.active_liquidity_supply / total_liquidity
        if total_liquidity > 0
        else 0.0
    )

    fee_rate_raw = current_pool.get("deposit_total_fee_rate")
    if fee_rate_raw is None:
        raise ValueError("decision-time deposit fee rate is missing")
    fee_rate_bps = int(str(fee_rate_raw)) * 10_000 / FEE_PRECISION

    scan = scan_chain_candidates(
        database_path,
        pool_address=pool_address,
        amount_x=amount_x,
        amount_y=amount_y,
        observation_limit=len(times),
        observation_times=times,
        half_widths=half_widths,
        center_offsets=center_offsets,
        strategies=strategy_values,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )
    baseline = select_deterministic_baseline(
        database_path,
        scan=scan,
        phase2_gate=None,
        config=BaselinePolicyConfig(
            estimated_network_cost_y_atomic=network_cost_y_atomic,
        ),
    ).research_choice

    rows: list[dict[str, Any]] = []
    drops: dict[str, int] = {}
    for candidate in scan.candidates:
        if candidate.status != "ACCEPTED" or candidate.replay is None:
            _bump(drops, "trailing_replay_rejected")
            continue
        if candidate.range_survival_ratio is None:
            _bump(drops, "trailing_survival_missing")
            continue

        try:
            trailing = replay_economics(
                candidate,
                entry_price_q64=_active_price(
                    store,
                    pool_address=pool_address,
                    observed_at=candidate.replay.start_observed_at,
                    active_bin_id=candidate.replay.start_active_bin_id,
                ),
                exit_price_q64=_active_price(
                    store,
                    pool_address=pool_address,
                    observed_at=candidate.replay.end_observed_at,
                    active_bin_id=candidate.replay.end_active_bin_id,
                ),
                network_cost_y_atomic=network_cost_y_atomic,
            )
        except ValueError:
            _bump(drops, "trailing_economics_incomplete")
            continue

        if (
            trailing.excess_vs_hold_bps is None
            or trailing.net_return_bps is None
        ):
            _bump(drops, "trailing_normalized_economics_missing")
            continue
        if trailing.has_unvalued_rewards:
            _bump(drops, "trailing_rewards_unvalued")
            continue

        strategy = candidate.strategy
        rows.append(
            {
                "pool_address": pool_address,
                "decision_observed_at": decision_time,
                "strategy": strategy,
                "half_width": int(candidate.half_width),
                "center_offset": int(candidate.center_offset),
                "baseline_selected": int(
                    baseline is not None
                    and candidate.strategy == baseline.strategy
                    and candidate.half_width == baseline.half_width
                    and candidate.center_offset == baseline.center_offset
                ),
                "strategy_spot": int(strategy == "SPOT"),
                "strategy_curve": int(strategy == "CURVE"),
                "strategy_bid_ask": int(strategy == "BID_ASK"),
                "range_width_bins": int(
                    candidate.max_bin_id - candidate.min_bin_id + 1
                ),
                "active_bin_id": active_id,
                "active_bin_move_1": active_id - previous_active_id,
                "deposit_fee_rate_bps": float(fee_rate_bps),
                "occupied_bins": shape.occupied_bins,
                "active_liquidity_ratio": float(active_ratio),
                "near_active_liquidity_ratio": (
                    shape.near_active_liquidity_ratio
                ),
                "below_active_liquidity_ratio": (
                    shape.below_active_liquidity_ratio
                ),
                "above_active_liquidity_ratio": (
                    shape.above_active_liquidity_ratio
                ),
                "liquidity_weighted_distance_bins": (
                    shape.liquidity_weighted_distance_bins
                ),
                "fee_growth_bins_x": activity.bins_with_x_growth,
                "fee_growth_bins_y": activity.bins_with_y_growth,
                "trailing_range_survival_ratio": float(
                    candidate.range_survival_ratio
                ),
                "trailing_excess_vs_hold_bps": int(
                    trailing.excess_vs_hold_bps
                ),
                "trailing_net_return_bps": int(
                    trailing.net_return_bps
                ),
                "trailing_max_observed_share_bps": int(
                    candidate.replay.max_observed_share_bps
                ),
            }
        )

    if not rows:
        raise ValueError(
            "current ML candidate frame has no replay-valid candidates"
        )

    return MLCurrentCandidateFrameReport(
        pool_address=pool_address,
        decision_observed_at=decision_time,
        previous_observed_at=previous_time,
        lookback_observations=len(times),
        candidates_seen=scan.attempted,
        candidates_built=len(rows),
        candidates_dropped=scan.attempted - len(rows),
        drop_reasons=tuple(sorted(drops.items())),
        rows=tuple(rows),
        no_lookahead=True,
    )
