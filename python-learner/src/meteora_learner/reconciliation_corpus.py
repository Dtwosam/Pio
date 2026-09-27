from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .reconciliation import (
    PositionAmountReconciliation,
    PositionFeeReconciliation,
    PositionRewardReconciliation,
    reconcile_position_amounts,
    reconcile_position_fee_interval,
    reconcile_position_reward_interval,
)
from .research_store import ResearchStore


@dataclass(frozen=True)
class PositionCorpusEntry:
    position_address: str
    amount_state: PositionAmountReconciliation | None
    amount_error: str | None
    fee_interval: PositionFeeReconciliation | None
    fee_interval_error: str | None
    reward_interval: PositionRewardReconciliation | None
    reward_interval_error: str | None


@dataclass(frozen=True)
class ReconciliationCorpusReport:
    positions_seen: int
    amount_positions_eligible: int
    amount_positions_exact: int
    amount_positions_provenance_ineligible: int
    amount_bins_checked: int
    amount_mismatched_bins: int
    amount_total_abs_error_x: int
    amount_total_abs_error_y: int
    fee_positions_with_two_snapshots: int
    fee_intervals_seen: int
    fee_intervals_eligible: int
    fee_intervals_exact: int
    fee_intervals_provenance_ineligible: int
    fee_bins_checked: int
    fee_mismatched_bins: int
    fee_total_abs_error_x: int
    fee_total_abs_error_y: int
    reward_intervals_seen: int
    reward_intervals_eligible: int
    reward_intervals_exact: int
    reward_intervals_provenance_ineligible: int
    reward_bins_checked: int
    reward_bins_with_checkpoint_growth: int
    reward_mismatched_bins: int
    reward_total_abs_error_one: int
    reward_total_abs_error_two: int
    amount_exact_rate: float | None
    fee_exact_rate: float | None
    reward_exact_rate: float | None
    strict_math_gate_passed: bool
    entries: tuple[PositionCorpusEntry, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _is_capture_provenance_error(message: str) -> bool:
    return (
        "capture-slot provenance" in message
        or "not single-context" in message
        or "capture slot must move forward" in message
    )


def build_reconciliation_corpus(
    database_path: str,
    *,
    position_limit: int | None = None,
) -> ReconciliationCorpusReport:
    """
    Aggregate exact DynamicPosition reconciliation across stored positions.

    Deterministic math is fail-closed:
    - amount-state bins must match exactly;
    - eligible unchanged-position fee intervals must match exactly;
    - eligible reward intervals must match exact effective reward checkpoints;
    - at least one reward bin must show real checkpoint growth.

    Ineligible intervals remain visible with their rejection reason.
    """
    store = ResearchStore(database_path)
    addresses = store.position_addresses(limit=position_limit)
    if not addresses:
        raise ValueError("no stored position snapshots available")

    entries: list[PositionCorpusEntry] = []

    amount_positions_eligible = 0
    amount_positions_exact = 0
    amount_positions_provenance_ineligible = 0
    amount_bins_checked = 0
    amount_mismatched_bins = 0
    amount_total_abs_error_x = 0
    amount_total_abs_error_y = 0

    fee_positions_with_two_snapshots = 0
    fee_intervals_seen = 0
    fee_intervals_eligible = 0
    fee_intervals_exact = 0
    fee_intervals_provenance_ineligible = 0
    fee_bins_checked = 0
    fee_mismatched_bins = 0
    fee_total_abs_error_x = 0
    fee_total_abs_error_y = 0

    reward_intervals_seen = 0
    reward_intervals_eligible = 0
    reward_intervals_exact = 0
    reward_intervals_provenance_ineligible = 0
    reward_bins_checked = 0
    reward_bins_with_checkpoint_growth = 0
    reward_mismatched_bins = 0
    reward_total_abs_error_one = 0
    reward_total_abs_error_two = 0

    for position_address in addresses:
        amount_state = None
        amount_error = None
        try:
            amount_state = reconcile_position_amounts(
                database_path,
                position_address=position_address,
            )
        except ValueError as exc:
            amount_error = str(exc)
            if _is_capture_provenance_error(amount_error):
                amount_positions_provenance_ineligible += 1
        else:
            amount_positions_eligible += 1
            amount_positions_exact += int(amount_state.exact_match)
            amount_bins_checked += amount_state.bins_checked
            amount_mismatched_bins += amount_state.mismatched_bins
            amount_total_abs_error_x += amount_state.total_abs_error_x
            amount_total_abs_error_y += amount_state.total_abs_error_y

        latest_fee_interval = None
        latest_reward_interval = None
        fee_errors: list[str] = []
        reward_errors: list[str] = []
        observation_times = store.position_observation_times(
            position_address,
            limit=None,
            ascending=True,
        )
        if len(observation_times) >= 2:
            fee_positions_with_two_snapshots += 1
            for start_time, end_time in zip(
                observation_times,
                observation_times[1:],
            ):
                fee_intervals_seen += 1
                try:
                    fee_interval = reconcile_position_fee_interval(
                        database_path,
                        position_address=position_address,
                        start_observed_at=start_time,
                        end_observed_at=end_time,
                    )
                except ValueError as exc:
                    message = str(exc)
                    fee_errors.append(
                        f"{start_time} -> {end_time}: {message}"
                    )
                    if _is_capture_provenance_error(message):
                        fee_intervals_provenance_ineligible += 1
                else:
                    latest_fee_interval = fee_interval
                    fee_intervals_eligible += 1
                    fee_intervals_exact += int(fee_interval.exact_match)
                    fee_bins_checked += fee_interval.bins_checked
                    fee_mismatched_bins += fee_interval.mismatched_bins
                    fee_total_abs_error_x += fee_interval.total_abs_error_x
                    fee_total_abs_error_y += fee_interval.total_abs_error_y

                reward_intervals_seen += 1
                try:
                    reward_interval = reconcile_position_reward_interval(
                        database_path,
                        position_address=position_address,
                        start_observed_at=start_time,
                        end_observed_at=end_time,
                    )
                except ValueError as exc:
                    message = str(exc)
                    reward_errors.append(
                        f"{start_time} -> {end_time}: {message}"
                    )
                    if _is_capture_provenance_error(message):
                        reward_intervals_provenance_ineligible += 1
                else:
                    latest_reward_interval = reward_interval
                    reward_intervals_eligible += 1
                    reward_intervals_exact += int(reward_interval.exact_match)
                    reward_bins_checked += reward_interval.bins_checked
                    reward_bins_with_checkpoint_growth += (
                        reward_interval.bins_with_checkpoint_growth
                    )
                    reward_mismatched_bins += reward_interval.mismatched_bins
                    reward_total_abs_error_one += reward_interval.total_abs_error_one
                    reward_total_abs_error_two += reward_interval.total_abs_error_two
        else:
            fee_errors.append(
                "need at least two position observations for fee reconciliation"
            )
            reward_errors.append(
                "need at least two position observations for reward reconciliation"
            )

        entries.append(
            PositionCorpusEntry(
                position_address=position_address,
                amount_state=amount_state,
                amount_error=amount_error,
                fee_interval=latest_fee_interval,
                fee_interval_error="; ".join(fee_errors) if fee_errors else None,
                reward_interval=latest_reward_interval,
                reward_interval_error=(
                    "; ".join(reward_errors) if reward_errors else None
                ),
            )
        )

    amount_exact_rate = (
        amount_positions_exact / amount_positions_eligible
        if amount_positions_eligible
        else None
    )
    fee_exact_rate = (
        fee_intervals_exact / fee_intervals_eligible
        if fee_intervals_eligible
        else None
    )
    reward_exact_rate = (
        reward_intervals_exact / reward_intervals_eligible
        if reward_intervals_eligible
        else None
    )
    strict_gate = (
        amount_positions_eligible > 0
        and fee_intervals_eligible > 0
        and reward_intervals_eligible > 0
        and reward_bins_with_checkpoint_growth > 0
        and amount_positions_exact == amount_positions_eligible
        and fee_intervals_exact == fee_intervals_eligible
        and reward_intervals_exact == reward_intervals_eligible
        and amount_mismatched_bins == 0
        and fee_mismatched_bins == 0
        and reward_mismatched_bins == 0
    )

    return ReconciliationCorpusReport(
        positions_seen=len(addresses),
        amount_positions_eligible=amount_positions_eligible,
        amount_positions_exact=amount_positions_exact,
        amount_positions_provenance_ineligible=(
            amount_positions_provenance_ineligible
        ),
        amount_bins_checked=amount_bins_checked,
        amount_mismatched_bins=amount_mismatched_bins,
        amount_total_abs_error_x=amount_total_abs_error_x,
        amount_total_abs_error_y=amount_total_abs_error_y,
        fee_positions_with_two_snapshots=fee_positions_with_two_snapshots,
        fee_intervals_seen=fee_intervals_seen,
        fee_intervals_eligible=fee_intervals_eligible,
        fee_intervals_exact=fee_intervals_exact,
        fee_intervals_provenance_ineligible=(
            fee_intervals_provenance_ineligible
        ),
        fee_bins_checked=fee_bins_checked,
        fee_mismatched_bins=fee_mismatched_bins,
        fee_total_abs_error_x=fee_total_abs_error_x,
        fee_total_abs_error_y=fee_total_abs_error_y,
        reward_intervals_seen=reward_intervals_seen,
        reward_intervals_eligible=reward_intervals_eligible,
        reward_intervals_exact=reward_intervals_exact,
        reward_intervals_provenance_ineligible=(
            reward_intervals_provenance_ineligible
        ),
        reward_bins_checked=reward_bins_checked,
        reward_bins_with_checkpoint_growth=reward_bins_with_checkpoint_growth,
        reward_mismatched_bins=reward_mismatched_bins,
        reward_total_abs_error_one=reward_total_abs_error_one,
        reward_total_abs_error_two=reward_total_abs_error_two,
        amount_exact_rate=amount_exact_rate,
        fee_exact_rate=fee_exact_rate,
        reward_exact_rate=reward_exact_rate,
        strict_math_gate_passed=strict_gate,
        entries=tuple(entries),
    )
