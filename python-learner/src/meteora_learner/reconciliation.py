from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .liquidity_math import (
    amounts_from_liquidity_share,
    fee_from_checkpoint_delta,
    reward_from_checkpoint_delta,
)
from .research_store import ResearchStore


DEFAULT_PUBKEY = "11111111111111111111111111111111"


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
    capture_slot: int
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
    start_capture_slot: int
    end_capture_slot: int
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
class PositionRewardBinCheck:
    bin_id: int
    liquidity_share: int
    reward_one_checkpoint_delta: int
    reward_two_checkpoint_delta: int
    predicted_reward_one_delta: int
    actual_reward_one_delta: int
    error_one: int
    predicted_reward_two_delta: int
    actual_reward_two_delta: int
    error_two: int


@dataclass(frozen=True)
class PositionRewardReconciliation:
    position_address: str
    start_observed_at: str
    end_observed_at: str
    start_capture_slot: int
    end_capture_slot: int
    reward_mint_0: str
    reward_mint_1: str
    bins_checked: int
    bins_with_checkpoint_growth: int
    mismatched_bins: int
    predicted_reward_one_delta: int
    actual_reward_one_delta: int
    predicted_reward_two_delta: int
    actual_reward_two_delta: int
    total_abs_error_one: int
    total_abs_error_two: int
    exact_match: bool
    bins: tuple[PositionRewardBinCheck, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PositionReconciliationReport:
    amount_state: PositionAmountReconciliation
    fee_interval: PositionFeeReconciliation | None
    reward_interval: PositionRewardReconciliation | None
    reward_interval_error: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _int(row: dict[str, Any], key: str) -> int:
    return int(str(row[key]))


def _single_context_capture_slot(
    snapshot: dict[str, Any],
    *,
    label: str,
) -> int:
    start_raw = snapshot.get("capture_slot_start")
    end_raw = snapshot.get("capture_slot_end")
    if start_raw is None or end_raw is None:
        raise ValueError(
            f"{label} position snapshot is missing capture-slot provenance"
        )
    start = int(start_raw)
    end = int(end_raw)
    if start < 0 or end < 0:
        raise ValueError(
            f"{label} position snapshot has negative capture slot"
        )
    if start != end:
        raise ValueError(
            f"{label} position snapshot is not single-context: "
            f"{start}..{end}"
        )
    return start


def _position_interval(
    store: ResearchStore,
    *,
    position_address: str,
    start_observed_at: str,
    end_observed_at: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
]:
    if start_observed_at >= end_observed_at:
        raise ValueError("reconciliation interval must move forward in time")

    start = store.position_snapshot_at(position_address, start_observed_at)
    end = store.position_snapshot_at(position_address, end_observed_at)
    if start is None or end is None:
        raise ValueError("missing position snapshot")
    start_capture_slot = _single_context_capture_slot(
        start,
        label="start",
    )
    end_capture_slot = _single_context_capture_slot(
        end,
        label="end",
    )
    if end_capture_slot <= start_capture_slot:
        raise ValueError(
            "reconciliation interval capture slot must move forward"
        )
    if str(start["pool_address"]) != str(end["pool_address"]):
        raise ValueError("position pool changed across reconciliation interval")
    if (
        int(start["lower_bin_id"]) != int(end["lower_bin_id"])
        or int(start["upper_bin_id"]) != int(end["upper_bin_id"])
    ):
        raise ValueError("position range changed across reconciliation interval")

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

    for bin_id in sorted(previous):
        if _int(previous[bin_id], "position_liquidity") != _int(
            current[bin_id],
            "position_liquidity",
        ):
            raise ValueError(
                f"position liquidity changed in bin {bin_id} across reconciliation interval"
            )

    return start, end, previous, current


def _iso_epoch(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def _has_position_event_between(
    store: ResearchStore,
    *,
    position_address: str,
    event_type: str,
    start_observed_at: str,
    end_observed_at: str,
) -> bool:
    start_epoch = _iso_epoch(start_observed_at)
    end_epoch = _iso_epoch(end_observed_at)
    return any(
        start_epoch < int(row["block_time"]) <= end_epoch
        for row in store.load_position_history_events(
            position_address,
            event_type=event_type,
        )
    )


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
    capture_slot = _single_context_capture_slot(
        snapshot,
        label="amount",
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
        capture_slot=capture_slot,
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
    store = ResearchStore(database_path)
    start, end, previous, current = _position_interval(
        store,
        position_address=position_address,
        start_observed_at=start_observed_at,
        end_observed_at=end_observed_at,
    )
    if (
        str(start["total_claimed_fee_x_amount"]) != str(end["total_claimed_fee_x_amount"])
        or str(start["total_claimed_fee_y_amount"]) != str(end["total_claimed_fee_y_amount"])
    ):
        raise ValueError("position claimed fees changed across reconciliation interval")

    checks: list[PositionFeeBinCheck] = []
    for bin_id in sorted(previous):
        before = previous[bin_id]
        after = current[bin_id]
        share = _int(before, "position_liquidity")

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
        start_capture_slot=_single_context_capture_slot(start, label="start"),
        end_capture_slot=_single_context_capture_slot(end, label="end"),
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


def reconcile_position_reward_interval(
    database_path: str,
    *,
    position_address: str,
    start_observed_at: str,
    end_observed_at: str,
) -> PositionRewardReconciliation:
    store = ResearchStore(database_path)
    start, end, previous, current = _position_interval(
        store,
        position_address=position_address,
        start_observed_at=start_observed_at,
        end_observed_at=end_observed_at,
    )

    metadata = (
        start.get("supports_limit_order"),
        start.get("reward_mint_0"),
        start.get("reward_mint_1"),
        end.get("supports_limit_order"),
        end.get("reward_mint_0"),
        end.get("reward_mint_1"),
    )
    if any(value is None for value in metadata):
        raise ValueError("reward campaign metadata missing; collect fresh position snapshots")
    if bool(start["supports_limit_order"]) or bool(end["supports_limit_order"]):
        raise ValueError("reward reconciliation is not applicable to limit-order pools")
    start_mints = (str(start["reward_mint_0"]), str(start["reward_mint_1"]))
    end_mints = (str(end["reward_mint_0"]), str(end["reward_mint_1"]))
    if start_mints != end_mints:
        raise ValueError("reward campaign mint changed across reconciliation interval")
    if start_mints == (DEFAULT_PUBKEY, DEFAULT_PUBKEY):
        raise ValueError("no active reward campaign in reconciliation interval")
    if _has_position_event_between(
        store,
        position_address=position_address,
        event_type="claim_reward",
        start_observed_at=start_observed_at,
        end_observed_at=end_observed_at,
    ):
        raise ValueError("position claimed rewards across reconciliation interval")

    checks: list[PositionRewardBinCheck] = []
    for bin_id in sorted(previous):
        before = previous[bin_id]
        after = current[bin_id]
        if not bool(before["reward_checkpoint_available"]) or not bool(
            after["reward_checkpoint_available"]
        ):
            raise ValueError(
                "reward checkpoint metadata missing; collect fresh position snapshots"
            )

        share = _int(before, "position_liquidity")
        checkpoint_one_before = _int(before, "bin_reward_per_token_stored_0")
        checkpoint_one_after = _int(after, "bin_reward_per_token_stored_0")
        checkpoint_two_before = _int(before, "bin_reward_per_token_stored_1")
        checkpoint_two_after = _int(after, "bin_reward_per_token_stored_1")
        if (
            checkpoint_one_after < checkpoint_one_before
            or checkpoint_two_after < checkpoint_two_before
        ):
            raise ValueError(f"reward checkpoint decreased in bin {bin_id}")

        delta_one = checkpoint_one_after - checkpoint_one_before
        delta_two = checkpoint_two_after - checkpoint_two_before
        predicted_one = reward_from_checkpoint_delta(
            liquidity_share=share,
            reward_per_token_delta=delta_one,
        )
        predicted_two = reward_from_checkpoint_delta(
            liquidity_share=share,
            reward_per_token_delta=delta_two,
        )
        actual_one = _int(after, "reward_one") - _int(before, "reward_one")
        actual_two = _int(after, "reward_two") - _int(before, "reward_two")
        if actual_one < 0 or actual_two < 0:
            raise ValueError(
                f"position rewards decreased in bin {bin_id}; claim/reset interval is ineligible"
            )

        checks.append(
            PositionRewardBinCheck(
                bin_id=bin_id,
                liquidity_share=share,
                reward_one_checkpoint_delta=delta_one,
                reward_two_checkpoint_delta=delta_two,
                predicted_reward_one_delta=predicted_one,
                actual_reward_one_delta=actual_one,
                error_one=predicted_one - actual_one,
                predicted_reward_two_delta=predicted_two,
                actual_reward_two_delta=actual_two,
                error_two=predicted_two - actual_two,
            )
        )

    mismatched = sum(item.error_one != 0 or item.error_two != 0 for item in checks)
    return PositionRewardReconciliation(
        position_address=position_address,
        start_observed_at=start_observed_at,
        end_observed_at=end_observed_at,
        start_capture_slot=_single_context_capture_slot(start, label="start"),
        end_capture_slot=_single_context_capture_slot(end, label="end"),
        reward_mint_0=start_mints[0],
        reward_mint_1=start_mints[1],
        bins_checked=len(checks),
        bins_with_checkpoint_growth=sum(
            item.reward_one_checkpoint_delta > 0
            or item.reward_two_checkpoint_delta > 0
            for item in checks
        ),
        mismatched_bins=mismatched,
        predicted_reward_one_delta=sum(item.predicted_reward_one_delta for item in checks),
        actual_reward_one_delta=sum(item.actual_reward_one_delta for item in checks),
        predicted_reward_two_delta=sum(item.predicted_reward_two_delta for item in checks),
        actual_reward_two_delta=sum(item.actual_reward_two_delta for item in checks),
        total_abs_error_one=sum(abs(item.error_one) for item in checks),
        total_abs_error_two=sum(abs(item.error_two) for item in checks),
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


def reconcile_latest_position_reward_interval(
    database_path: str,
    *,
    position_address: str,
) -> PositionRewardReconciliation:
    store = ResearchStore(database_path)
    times = store.position_observation_times(position_address, limit=2)
    if len(times) < 2:
        raise ValueError("need at least two position observations for reward reconciliation")
    end_time, start_time = times[0], times[1]
    return reconcile_position_reward_interval(
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
    reward_interval = None
    reward_interval_error = None
    if len(store.position_observation_times(position_address, limit=2)) >= 2:
        fee_interval = reconcile_latest_position_fee_interval(
            database_path,
            position_address=position_address,
        )
        try:
            reward_interval = reconcile_latest_position_reward_interval(
                database_path,
                position_address=position_address,
            )
        except ValueError as exc:
            reward_interval_error = str(exc)

    return PositionReconciliationReport(
        amount_state=amount_state,
        fee_interval=fee_interval,
        reward_interval=reward_interval,
        reward_interval_error=reward_interval_error,
    )
