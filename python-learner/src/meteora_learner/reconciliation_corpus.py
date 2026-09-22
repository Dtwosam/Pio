from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .reconciliation import (
    PositionAmountReconciliation,
    PositionFeeReconciliation,
    reconcile_position_fee_interval,
    reconcile_position_amounts,
)
from .research_store import ResearchStore


@dataclass(frozen=True)
class PositionCorpusEntry:
    position_address: str
    amount_state: PositionAmountReconciliation | None
    amount_error: str | None
    fee_interval: PositionFeeReconciliation | None
    fee_interval_error: str | None


@dataclass(frozen=True)
class ReconciliationCorpusReport:
    positions_seen: int
    amount_positions_eligible: int
    amount_positions_exact: int
    amount_bins_checked: int
    amount_mismatched_bins: int
    amount_total_abs_error_x: int
    amount_total_abs_error_y: int
    fee_positions_with_two_snapshots: int
    fee_intervals_seen: int
    fee_intervals_eligible: int
    fee_intervals_exact: int
    fee_bins_checked: int
    fee_mismatched_bins: int
    fee_total_abs_error_x: int
    fee_total_abs_error_y: int
    amount_exact_rate: float | None
    fee_exact_rate: float | None
    strict_math_gate_passed: bool
    entries: tuple[PositionCorpusEntry, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_reconciliation_corpus(
    database_path: str,
    *,
    position_limit: int | None = None,
) -> ReconciliationCorpusReport:
    """
    Aggregate exact DynamicPosition reconciliation across stored positions.

    The strict gate is intentionally binary for deterministic math:
    - every eligible amount-state bin must match exactly;
    - every eligible unchanged-position fee interval must match exactly;
    - at least one amount-state position and one fee interval must be eligible.

    Ineligible fee intervals are preserved with their reason rather than treated
    as passes or silently removed.
    """
    store = ResearchStore(database_path)
    addresses = store.position_addresses(limit=position_limit)
    if not addresses:
        raise ValueError("no stored position snapshots available")

    entries: list[PositionCorpusEntry] = []

    amount_positions_eligible = 0
    amount_positions_exact = 0
    amount_bins_checked = 0
    amount_mismatched_bins = 0
    amount_total_abs_error_x = 0
    amount_total_abs_error_y = 0

    fee_positions_with_two_snapshots = 0
    fee_intervals_seen = 0
    fee_intervals_eligible = 0
    fee_intervals_exact = 0
    fee_bins_checked = 0
    fee_mismatched_bins = 0
    fee_total_abs_error_x = 0
    fee_total_abs_error_y = 0

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
        else:
            amount_positions_eligible += 1
            amount_positions_exact += int(amount_state.exact_match)
            amount_bins_checked += amount_state.bins_checked
            amount_mismatched_bins += amount_state.mismatched_bins
            amount_total_abs_error_x += amount_state.total_abs_error_x
            amount_total_abs_error_y += amount_state.total_abs_error_y

        latest_fee_interval = None
        fee_errors: list[str] = []
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
                    fee_errors.append(
                        f"{start_time} -> {end_time}: {exc}"
                    )
                    continue

                latest_fee_interval = fee_interval
                fee_intervals_eligible += 1
                fee_intervals_exact += int(fee_interval.exact_match)
                fee_bins_checked += fee_interval.bins_checked
                fee_mismatched_bins += fee_interval.mismatched_bins
                fee_total_abs_error_x += fee_interval.total_abs_error_x
                fee_total_abs_error_y += fee_interval.total_abs_error_y
        else:
            fee_errors.append(
                "need at least two position observations for fee reconciliation"
            )

        entries.append(
            PositionCorpusEntry(
                position_address=position_address,
                amount_state=amount_state,
                amount_error=amount_error,
                fee_interval=latest_fee_interval,
                fee_interval_error="; ".join(fee_errors) if fee_errors else None,
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
    strict_gate = (
        amount_positions_eligible > 0
        and fee_intervals_eligible > 0
        and amount_positions_exact == amount_positions_eligible
        and fee_intervals_exact == fee_intervals_eligible
        and amount_mismatched_bins == 0
        and fee_mismatched_bins == 0
    )

    return ReconciliationCorpusReport(
        positions_seen=len(addresses),
        amount_positions_eligible=amount_positions_eligible,
        amount_positions_exact=amount_positions_exact,
        amount_bins_checked=amount_bins_checked,
        amount_mismatched_bins=amount_mismatched_bins,
        amount_total_abs_error_x=amount_total_abs_error_x,
        amount_total_abs_error_y=amount_total_abs_error_y,
        fee_positions_with_two_snapshots=fee_positions_with_two_snapshots,
        fee_intervals_seen=fee_intervals_seen,
        fee_intervals_eligible=fee_intervals_eligible,
        fee_intervals_exact=fee_intervals_exact,
        fee_bins_checked=fee_bins_checked,
        fee_mismatched_bins=fee_mismatched_bins,
        fee_total_abs_error_x=fee_total_abs_error_x,
        fee_total_abs_error_y=fee_total_abs_error_y,
        amount_exact_rate=amount_exact_rate,
        fee_exact_rate=fee_exact_rate,
        strict_math_gate_passed=strict_gate,
        entries=tuple(entries),
    )
