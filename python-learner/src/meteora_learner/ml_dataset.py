from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

import pandas as pd

from .baseline_walk_forward import BaselineWalkForwardReport
from .chain_features import fee_checkpoint_activity, summarize_liquidity_shape
from .composition_fee import FEE_PRECISION
from .research_store import ResearchStore


ML_FEATURE_COLUMNS = (
    "strategy_spot",
    "strategy_curve",
    "strategy_bid_ask",
    "half_width",
    "center_offset",
    "range_width_bins",
    "active_bin_id",
    "active_bin_move_1",
    "deposit_fee_rate_bps",
    "occupied_bins",
    "active_liquidity_ratio",
    "near_active_liquidity_ratio",
    "below_active_liquidity_ratio",
    "above_active_liquidity_ratio",
    "liquidity_weighted_distance_bins",
    "fee_growth_bins_x",
    "fee_growth_bins_y",
    "trailing_range_survival_ratio",
    "trailing_excess_vs_hold_bps",
    "trailing_net_return_bps",
    "trailing_max_observed_share_bps",
)

ML_TARGET_COLUMNS = (
    "target_net_return_bps",
    "target_excess_vs_hold_bps",
    "target_range_survival_ratio",
    "target_positive_excess",
)


@dataclass(frozen=True)
class MLTrainingExample:
    pool_address: str
    decision_observed_at: str
    forward_end_observed_at: str
    strategy: str
    baseline_selected: int
    strategy_spot: int
    strategy_curve: int
    strategy_bid_ask: int
    half_width: int
    center_offset: int
    range_width_bins: int
    active_bin_id: int
    active_bin_move_1: int
    deposit_fee_rate_bps: float
    occupied_bins: int
    active_liquidity_ratio: float
    near_active_liquidity_ratio: float
    below_active_liquidity_ratio: float
    above_active_liquidity_ratio: float
    liquidity_weighted_distance_bins: float
    fee_growth_bins_x: int
    fee_growth_bins_y: int
    trailing_range_survival_ratio: float
    trailing_excess_vs_hold_bps: int
    trailing_net_return_bps: int
    trailing_max_observed_share_bps: int
    target_net_return_bps: int
    target_excess_vs_hold_bps: int
    target_range_survival_ratio: float
    target_positive_excess: int


@dataclass(frozen=True)
class MLDatasetReport:
    reports_seen: int
    steps_seen: int
    examples_built: int
    steps_dropped: int
    drop_reasons: tuple[tuple[str, int], ...]
    examples: tuple[MLTrainingExample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(asdict(item) for item in self.examples)


def _bump(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def build_ml_dataset(
    database_path: str,
    reports: Sequence[BaselineWalkForwardReport],
    *,
    near_liquidity_radius: int = 5,
) -> MLDatasetReport:
    """
    Build no-lookahead supervised examples from deterministic walk-forward steps.

    Feature state is read at or before decision_observed_at. Forward economics
    and range survival are used only as labels.
    """
    if not reports:
        raise ValueError("at least one walk-forward report is required")
    if near_liquidity_radius < 0:
        raise ValueError("near_liquidity_radius cannot be negative")

    store = ResearchStore(database_path)
    examples: list[MLTrainingExample] = []
    drops: dict[str, int] = {}
    steps_seen = 0

    for report in reports:
        times = store.chain_observation_times(
            report.pool_address,
            limit=None,
            ascending=True,
        )
        time_index = {value: index for index, value in enumerate(times)}

        for step in report.steps_detail:
            steps_seen += 1
            if not step.selected or step.proposal is None:
                _bump(drops, "no_selected_proposal")
                continue
            if step.forward_status != "VALID" or step.forward_economics is None:
                _bump(drops, "forward_economics_incomplete")
                continue
            if step.forward_range_survival_ratio is None:
                _bump(drops, "forward_range_survival_missing")
                continue

            economics = step.forward_economics
            if (
                economics.net_return_bps is None
                or economics.excess_vs_hold_initial_bps is None
            ):
                _bump(drops, "forward_normalized_targets_missing")
                continue
            if (
                step.trailing_range_survival_ratio is None
                or step.trailing_excess_vs_hold_bps is None
                or step.trailing_net_return_bps is None
                or step.trailing_max_observed_share_bps is None
            ):
                _bump(drops, "trailing_features_missing")
                continue

            decision_index = time_index.get(step.decision_observed_at)
            if decision_index is None or decision_index == 0:
                _bump(drops, "decision_has_no_prior_chain_observation")
                continue
            previous_time = times[decision_index - 1]

            current_pool = store.chain_pool_snapshot_at(
                report.pool_address,
                step.decision_observed_at,
            )
            previous_pool = store.chain_pool_snapshot_at(
                report.pool_address,
                previous_time,
            )
            if current_pool is None or previous_pool is None:
                _bump(drops, "decision_pool_state_missing")
                continue

            current_bins = store.load_bin_liquidity(
                report.pool_address,
                observed_at=step.decision_observed_at,
            )
            previous_bins = store.load_bin_liquidity(
                report.pool_address,
                observed_at=previous_time,
            )
            if not current_bins or not previous_bins:
                _bump(drops, "decision_bin_state_missing")
                continue

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
                _bump(drops, "deposit_fee_rate_missing")
                continue
            fee_rate_bps = int(str(fee_rate_raw)) * 10_000 / FEE_PRECISION

            strategy = step.proposal.strategy
            examples.append(
                MLTrainingExample(
                    pool_address=report.pool_address,
                    decision_observed_at=step.decision_observed_at,
                    forward_end_observed_at=step.forward_end_observed_at,
                    strategy=strategy,
                    baseline_selected=1,
                    strategy_spot=int(strategy == "SPOT"),
                    strategy_curve=int(strategy == "CURVE"),
                    strategy_bid_ask=int(strategy == "BID_ASK"),
                    half_width=step.proposal.half_width,
                    center_offset=step.proposal.center_offset,
                    range_width_bins=(
                        step.proposal.max_bin_id - step.proposal.min_bin_id + 1
                    ),
                    active_bin_id=active_id,
                    active_bin_move_1=(
                        active_id - int(previous_pool["active_bin_id"])
                    ),
                    deposit_fee_rate_bps=float(fee_rate_bps),
                    occupied_bins=shape.occupied_bins,
                    active_liquidity_ratio=float(active_ratio),
                    near_active_liquidity_ratio=shape.near_active_liquidity_ratio,
                    below_active_liquidity_ratio=shape.below_active_liquidity_ratio,
                    above_active_liquidity_ratio=shape.above_active_liquidity_ratio,
                    liquidity_weighted_distance_bins=(
                        shape.liquidity_weighted_distance_bins
                    ),
                    fee_growth_bins_x=activity.bins_with_x_growth,
                    fee_growth_bins_y=activity.bins_with_y_growth,
                    trailing_range_survival_ratio=(
                        step.trailing_range_survival_ratio
                    ),
                    trailing_excess_vs_hold_bps=(
                        step.trailing_excess_vs_hold_bps
                    ),
                    trailing_net_return_bps=step.trailing_net_return_bps,
                    trailing_max_observed_share_bps=(
                        step.trailing_max_observed_share_bps
                    ),
                    target_net_return_bps=economics.net_return_bps,
                    target_excess_vs_hold_bps=(
                        economics.excess_vs_hold_initial_bps
                    ),
                    target_range_survival_ratio=(
                        step.forward_range_survival_ratio
                    ),
                    target_positive_excess=int(
                        economics.excess_vs_hold_initial_bps > 0
                    ),
                )
            )

    return MLDatasetReport(
        reports_seen=len(reports),
        steps_seen=steps_seen,
        examples_built=len(examples),
        steps_dropped=steps_seen - len(examples),
        drop_reasons=tuple(sorted(drops.items())),
        examples=tuple(examples),
    )
