from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .liquidity_math import amounts_from_liquidity_share, fee_from_checkpoint_delta
from .research_store import ResearchStore


@dataclass(frozen=True)
class PositionAmountBinCheck:
    bin_id: int
    liquidity_share: int
    bin_liquidity_supply: int
    predicted_x: int
    actual_x: int
    error_x: int
    predicted_y: int
    actual_y: int
    error_y: int


@dataclass(frozen=True)
class PositionAmountReconciliation:
    position_address: str
    observed_at: str
    bins_checked: int
    mismatched_bins: int
    total_abs_error_x: int
    total_abs_error_y: int
    exact_match: bool
    bins: tuple[PositionAmountBinCheck, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PositionFeeBinCheck:
    bin_id: int
    liquidity_share: int
    fee_x_checkpoint_delta: int
    fee_y_checkpoint_delta: int
    predicted_fee_x_delta: int
    actual_fee_x_delta: int
    error_x: int
    predicted_fee_y_delta: int
    actual_fee_y_delta: int
    error_y: int


@dataclass(frozen=True)
class PositionFeeReconciliation:
    position_address: str
    start_observed_at: str
    end_observed_at: str
    bins_checked: int
    mismatched_bins: int
    predicted_fee_x_delta: int
    actual_fee_x_delta: int
    predicted_fee_y_delta: int
    actual_fee_y_delta: int
    total_abs_error_x: int
    total_abs_error_y: int
    exact_match: bool
    bins: tuple[PositionFeeBinCheck, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PositionReconciliationReport:
    amount_state: PositionAmountReconciliation
    fee_interval: PositionFeeReconciliation | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _int(row: dict[str, Any], key: str) -> int:
    return int(str(row[key]))


def reconcile_position_amounts(
    database_path: str,
    *,
    position_address: str,
    observed_at: str | None = None,
) -> PositionAmountReconciliation:
    store = ResearchStore(database_path)
    if observed_at is None:
        snapshot = store.latest_position_snapshot(position_address)
        if snapshot is None:
            raise ValueError(f"no stored position snapshot for {position_address}")
        observed_at = str(snapshot["observed_at"])
    else:
        snapshot = store.position_snapshot_at(position_address, observed_at)
        if snapshot is None:
            raise ValueError(
                f"no stored position snapshot for {position_address} at {observed_at}"
            )

    rows = store.load_position_bins(position_address, observed_at=observed_at)
    if not rows:
        raise ValueError("position snapshot has no bin rows")

    checks: list[PositionAmountBinCheck] = []
    for row in rows:
        share = _int(row, "position_liquidity")
        supply = _int(row, "bin_liquidity")
        predicted_x, predicted_y = amounts_from_liquidity_share(
            liquidity_share=share,
            bin_amount_x=_int(row, "bin_x_amount"),
            bin_amount_y=_int(row, "bin_y_amount"),
            liquidity_supply=supply,
        )
        actual_x = _int(row, "position_x_amount")
        actual_y = _int(row, "position_y_amount")
        checks.append(
            PositionAmountBinCheck(
                bin_id=int(row["bin_id"]),
                liquidity_share=share,
                bin_liquidity_supply=supply,
                predicted_x=predicted_x,
                actual_x=actual_x,
                error_x=predicted_x - actual_x,
                predicted_y=predicted_y,
                actual_y=actual_y,
                error_y=predicted_y - actual_y,
            )
        )

    mismatched = sum(item.error_x != 0 or item.error_y != 0 for item in checks)
    return PositionAmountReconciliation(
        position_address=position_address,
        observed_at=observed_at,
        bins_checked=len(checks),
        mismatched_bins=mismatched,
        total_abs_error_x=sum(abs(item.error_x) for item in checks),
        total_abs_error_y=sum(abs(item.error_y) for item in checks),
        exact_match=mismatched == 0,
        bins=tuple(checks),
    )


def reconcile_position_fee_interval(
    database_path: str,
    *,
    position_address: str,
    start_observed_at: str,
    end_observed_at: str,
) -> PositionFeeReconciliation:
    if start_observed_at >= end_observed_at:
        raise ValueError("fee reconciliation interval must move forward in time")

    store = ResearchStore(database_path)
    start = store.position_snapshot_at(position_address, start_observed_at)
    end = store.position_snapshot_at(position_address, end_observed_at)
    if start is None or end is None:
        raise ValueError("missing position snapshot")

    if str(start["pool_address"]) != str(end["pool_address"]):
        raise ValueError("position pool changed across reconciliation interval")
    if (
        int(start["lower_bin_id"]) != int(end["lower_bin_id"])
        or int(start["upper_bin_id"]) != int(end["upper_bin_id"])
    ):
        raise ValueError("position range changed across reconciliation interval")
    if (
        str(start["total_claimed_fee_x_amount"]) != str(end["total_claimed_fee_x_amount"])
        or str(start["total_claimed_fee_y_amount"]) != str(end["total_claimed_fee_y_amount"])
    ):
        raise ValueError("position claimed fees changed across reconciliation interval")

    previous = {
        int(row["bin_id"]): row
        for row in store.load_position_bins(
            position_address,
            observed_at=start_observed_at,
        )
    }
    current = {
        int(row["bin_id"]): row
        for row in store.load_position_bins(
            position_address,
            observed_at=end_observed_at,
        )
    }
    if set(previous) != set(current):
        raise ValueError("position bin coverage changed across reconciliation interval")

    checks: list[PositionFeeBinCheck] = []
    for bin_id in sorted(previous):
        before = previous[bin_id]
        after = current[bin_id]
        share = _int(before, "position_liquidity")
        if share != _int(after, "position_liquidity"):
            raise ValueError(
                f"position liquidity changed in bin {bin_id} across reconciliation interval"
            )

        checkpoint_x_before = _int(before, "bin_fee_x_per_token_stored")
        checkpoint_x_after = _int(after, "bin_fee_x_per_token_stored")
        checkpoint_y_before = _int(before, "bin_fee_y_per_token_stored")
        checkpoint_y_after = _int(after, "bin_fee_y_per_token_stored")
        if checkpoint_x_after < checkpoint_x_before or checkpoint_y_after < checkpoint_y_before:
            raise ValueError(f"fee checkpoint decreased in bin {bin_id}")

        delta_x = checkpoint_x_after - checkpoint_x_before
        delta_y = checkpoint_y_after - checkpoint_y_before
        predicted_x = fee_from_checkpoint_delta(
            liquidity_share=share,
            fee_per_token_delta=delta_x,
        )
        predicted_y = fee_from_checkpoint_delta(
            liquidity_share=share,
            fee_per_token_delta=delta_y,
        )
        actual_x = _int(after, "position_fee_x_amount") - _int(
            before, "position_fee_x_amount"
        )
        actual_y = _int(after, "position_fee_y_amount") - _int(
            before, "position_fee_y_amount"
        )

        checks.append(
            PositionFeeBinCheck(
                bin_id=bin_id,
                liquidity_share=share,
                fee_x_checkpoint_delta=delta_x,
                fee_y_checkpoint_delta=delta_y,
                predicted_fee_x_delta=predicted_x,
                actual_fee_x_delta=actual_x,
                error_x=predicted_x - actual_x,
                predicted_fee_y_delta=predicted_y,
                actual_fee_y_delta=actual_y,
                error_y=predicted_y - actual_y,
            )
        )

    mismatched = sum(item.error_x != 0 or item.error_y != 0 for item in checks)
    return PositionFeeReconciliation(
        position_address=position_address,
        start_observed_at=start_observed_at,
        end_observed_at=end_observed_at,
        bins_checked=len(checks),
        mismatched_bins=mismatched,
        predicted_fee_x_delta=sum(item.predicted_fee_x_delta for item in checks),
        actual_fee_x_delta=sum(item.actual_fee_x_delta for item in checks),
        predicted_fee_y_delta=sum(item.predicted_fee_y_delta for item in checks),
        actual_fee_y_delta=sum(item.actual_fee_y_delta for item in checks),
        total_abs_error_x=sum(abs(item.error_x) for item in checks),
        total_abs_error_y=sum(abs(item.error_y) for item in checks),
        exact_match=mismatched == 0,
        bins=tuple(checks),
    )


def reconcile_latest_position_fee_interval(
    database_path: str,
    *,
    position_address: str,
) -> PositionFeeReconciliation:
    store = ResearchStore(database_path)
    times = store.position_observation_times(position_address, limit=2)
    if len(times) < 2:
        raise ValueError("need at least two position observations for fee reconciliation")

    end_time, start_time = times[0], times[1]
    return reconcile_position_fee_interval(
        database_path,
        position_address=position_address,
        start_observed_at=start_time,
        end_observed_at=end_time,
    )

def reconcile_position(
    database_path: str,
    *,
    position_address: str,
) -> PositionReconciliationReport:
    amount_state = reconcile_position_amounts(
        database_path,
        position_address=position_address,
    )
    store = ResearchStore(database_path)
    fee_interval = None
    if len(store.position_observation_times(position_address, limit=2)) >= 2:
        fee_interval = reconcile_latest_position_fee_interval(
            database_path,
            position_address=position_address,
        )
    return PositionReconciliationReport(
        amount_state=amount_state,
        fee_interval=fee_interval,
    )
