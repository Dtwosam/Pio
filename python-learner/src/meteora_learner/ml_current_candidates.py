from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import pandas as pd

from .baseline_policy import replay_economics
from .chain_features import fee_checkpoint_activity, summarize_liquidity_shape
from .chain_scan import scan_chain_candidates
from .composition_fee import FEE_PRECISION
from .ml_dataset import ML_FEATURE_COLUMNS
from .ml_inference import MLInferenceConfig, MLInferenceReport, score_ml_candidates
from .ml_workflow import load_registered_ml_v1
from .research_store import ResearchStore
from .retraining_cycle import active_retraining_cycle
from .storage import Storage
from .strategy import StrategyType


@dataclass(frozen=True)
class MLCurrentCandidateFrameReport:
    pool_address: str
    decision_observed_at: str
    previous_observed_at: str
    observation_count: int
    candidates_seen: int
    candidates_built: int
    candidates_dropped: int
    drop_reasons: tuple[tuple[str, int], ...]
    rows: tuple[dict[str, Any], ...]
    research_only: bool
    policy_actionable: bool
    live_authorized: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


@dataclass(frozen=True)
class MLCurrentPaperChallengerDecision:
    cycle_id: str
    model_id: str
    champion_model_id: str
    target_dataset_version: str
    pool_address: str
    decision_observed_at: str
    frame: MLCurrentCandidateFrameReport
    inference: MLInferenceReport
    selection_ready: bool
    paper_entry_authorized: bool
    paper_only: bool
    research_only: bool
    policy_actionable: bool
    live_authorized: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _bump(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("ML current-candidate timestamps require timezone")
    return parsed.astimezone(timezone.utc)


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
        raise ValueError("active-bin price missing")
    price = int(str(row["price"]))
    if price <= 0:
        raise ValueError("active-bin price must be positive")
    return price


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
    strategies: Iterable[StrategyType | str] = (
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
    Build no-lookahead feature rows for the latest eligible chain decision.

    Every feature uses the selected current observation, the immediately prior
    observation, or trailing replay ending at the current observation. No
    forward labels or future chain snapshots are read.
    """
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if amount_x < 0 or amount_y < 0 or (amount_x == 0 and amount_y == 0):
        raise ValueError("at least one non-negative token amount must be positive")
    if network_cost_y_atomic < 0:
        raise ValueError("network_cost_y_atomic cannot be negative")
    if lookback_observations < 2:
        raise ValueError("lookback_observations must be at least 2")
    if near_liquidity_radius < 0:
        raise ValueError("near_liquidity_radius cannot be negative")

    normalized_strategies = tuple(StrategyType(value) for value in strategies)
    if not normalized_strategies:
        raise ValueError("at least one strategy is required")

    store = ResearchStore(database_path)
    times = store.chain_observation_times(
        pool_address,
        limit=None,
        ascending=True,
    )
    if as_of is not None:
        cutoff = _parse_time(as_of)
        times = [item for item in times if _parse_time(item) <= cutoff]
    if len(times) < lookback_observations:
        raise ValueError(
            "not enough chain observations for current ML candidate frame"
        )

    selected_times = times[-lookback_observations:]
    previous_time = selected_times[-2]
    decision_time = selected_times[-1]

    current_pool = store.chain_pool_snapshot_at(pool_address, decision_time)
    previous_pool = store.chain_pool_snapshot_at(pool_address, previous_time)
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
        raise ValueError("current ML candidate chain state is incomplete")

    active_id = int(current_pool["active_bin_id"])
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
        raise ValueError("current ML candidate deposit fee rate is missing")
    fee_rate_bps = int(str(fee_rate_raw)) * 10_000 / FEE_PRECISION

    scan = scan_chain_candidates(
        database_path,
        pool_address=pool_address,
        amount_x=amount_x,
        amount_y=amount_y,
        observation_limit=len(selected_times),
        observation_times=selected_times,
        half_widths=half_widths,
        center_offsets=center_offsets,
        strategies=normalized_strategies,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )

    rows: list[dict[str, Any]] = []
    drops: dict[str, int] = {}
    for candidate in scan.candidates:
        if candidate.status != "ACCEPTED" or candidate.replay is None:
            _bump(drops, "trailing_replay_rejected")
            continue
        if candidate.range_survival_ratio is None:
            _bump(drops, "trailing_survival_missing")
            continue

        replay = candidate.replay
        try:
            trailing = replay_economics(
                candidate,
                entry_price_q64=_active_price(
                    store,
                    pool_address=pool_address,
                    observed_at=replay.start_observed_at,
                    active_bin_id=replay.start_active_bin_id,
                ),
                exit_price_q64=_active_price(
                    store,
                    pool_address=pool_address,
                    observed_at=replay.end_observed_at,
                    active_bin_id=replay.end_active_bin_id,
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
                "strategy_spot": int(strategy == "SPOT"),
                "strategy_curve": int(strategy == "CURVE"),
                "strategy_bid_ask": int(strategy == "BID_ASK"),
                "half_width": candidate.half_width,
                "center_offset": candidate.center_offset,
                "range_width_bins": (
                    candidate.max_bin_id - candidate.min_bin_id + 1
                ),
                "active_bin_id": active_id,
                "active_bin_move_1": (
                    active_id - int(previous_pool["active_bin_id"])
                ),
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
                "trailing_range_survival_ratio": (
                    candidate.range_survival_ratio
                ),
                "trailing_excess_vs_hold_bps": (
                    trailing.excess_vs_hold_bps
                ),
                "trailing_net_return_bps": trailing.net_return_bps,
                "trailing_max_observed_share_bps": (
                    replay.max_observed_share_bps
                ),
            }
        )

    missing_feature_rows = [
        index
        for index, row in enumerate(rows)
        if any(column not in row for column in ML_FEATURE_COLUMNS)
    ]
    if missing_feature_rows:
        raise ValueError(
            "current ML candidate frame is missing model features"
        )

    return MLCurrentCandidateFrameReport(
        pool_address=pool_address,
        decision_observed_at=decision_time,
        previous_observed_at=previous_time,
        observation_count=len(selected_times),
        candidates_seen=scan.attempted,
        candidates_built=len(rows),
        candidates_dropped=scan.attempted - len(rows),
        drop_reasons=tuple(sorted(drops.items())),
        rows=tuple(rows),
        research_only=True,
        policy_actionable=False,
        live_authorized=False,
    )


def score_current_registered_paper_challenger(
    storage: Storage,
    *,
    cycle_id: str,
    model_id: str,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    network_cost_y_atomic: int,
    lookback_observations: int = 12,
    half_widths: Sequence[int] = (0, 1, 2, 5, 10),
    center_offsets: Sequence[int] = (0,),
    strategies: Iterable[StrategyType | str] = (
        StrategyType.SPOT,
        StrategyType.CURVE,
        StrategyType.BID_ASK,
    ),
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
    near_liquidity_radius: int = 5,
    as_of: str | None = None,
    inference_config: MLInferenceConfig = MLInferenceConfig(),
) -> MLCurrentPaperChallengerDecision:
    if not cycle_id.strip() or not model_id.strip():
        raise ValueError("cycle_id and model_id are required")

    cycle = active_retraining_cycle(storage)
    if cycle is None or cycle.cycle_id != cycle_id:
        raise ValueError("named retraining cycle is not the active cycle")
    if cycle.status != "PAPER_CHALLENGER":
        raise ValueError("active retraining cycle is not PAPER_CHALLENGER")
    if cycle.challenger_model_id != model_id:
        raise ValueError("active retraining cycle challenger/model mismatch")

    model = storage.model_registry_entry(model_id)
    if model is None:
        raise ValueError(f"unknown model_id: {model_id}")
    if str(model["status"]) != "PAPER_CHALLENGER":
        raise ValueError("registered challenger is not PAPER_CHALLENGER")
    if str(model["dataset_version"]) != cycle.target_dataset_version:
        raise ValueError(
            "PAPER challenger dataset version does not match active cycle"
        )

    bundle = load_registered_ml_v1(storage, model_id=model_id)
    if tuple(bundle.feature_columns) != tuple(ML_FEATURE_COLUMNS):
        raise ValueError("registered PAPER challenger feature contract changed")

    frame = build_current_ml_candidate_frame(
        str(storage.path),
        pool_address=pool_address,
        amount_x=amount_x,
        amount_y=amount_y,
        network_cost_y_atomic=network_cost_y_atomic,
        lookback_observations=lookback_observations,
        half_widths=half_widths,
        center_offsets=center_offsets,
        strategies=strategies,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
        near_liquidity_radius=near_liquidity_radius,
        as_of=as_of,
    )
    if not frame.rows:
        raise ValueError("current ML candidate frame has no usable rows")

    inference = score_ml_candidates(
        bundle,
        frame.to_frame(),
        config=inference_config,
    )
    if inference.policy_actionable:
        raise ValueError("ML current inference crossed research-only boundary")

    return MLCurrentPaperChallengerDecision(
        cycle_id=cycle.cycle_id,
        model_id=model_id,
        champion_model_id=cycle.champion_model_id,
        target_dataset_version=cycle.target_dataset_version,
        pool_address=pool_address,
        decision_observed_at=frame.decision_observed_at,
        frame=frame,
        inference=inference,
        selection_ready=inference.research_choice is not None,
        paper_entry_authorized=False,
        paper_only=True,
        research_only=True,
        policy_actionable=False,
        live_authorized=False,
    )
