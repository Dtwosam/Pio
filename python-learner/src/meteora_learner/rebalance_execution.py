from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any

from .research_store import ResearchStore


@dataclass(frozen=True)
class RebalanceExecutionSample:
    signature: str
    instruction_index: int
    observed_active_id: int | None
    actual_active_id: int
    active_bin_drift: int | None
    max_active_bin_slippage: int | None
    active_bin_guard_passed: bool | None
    min_withdraw_x_amount: int | None
    actual_withdraw_x_amount: int
    withdraw_x_headroom: int | None
    withdraw_x_guard_passed: bool | None
    max_deposit_x_amount: int | None
    actual_deposit_x_amount: int
    deposit_x_headroom: int | None
    deposit_x_guard_passed: bool | None
    min_withdraw_y_amount: int | None
    actual_withdraw_y_amount: int
    withdraw_y_headroom: int | None
    withdraw_y_guard_passed: bool | None
    max_deposit_y_amount: int | None
    actual_deposit_y_amount: int
    deposit_y_headroom: int | None
    deposit_y_guard_passed: bool | None
    x_fee_amount: int
    y_fee_amount: int
    network_fee_lamports: int | None
    compute_units_consumed: int | None
    request_decoded: bool
    all_guards_passed: bool | None


@dataclass(frozen=True)
class RebalanceExecutionCalibrationReport:
    position_address: str
    rebalance_events: int
    request_decodes: int
    guard_samples: int
    guard_passes: int
    guard_violations: int
    request_coverage_rate: float
    mean_network_fee_lamports: float | None
    mean_compute_units: float | None
    samples: tuple[RebalanceExecutionSample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_rebalance_execution_calibration(
    database_path: str,
    *,
    position_address: str,
) -> RebalanceExecutionCalibrationReport:
    store = ResearchStore(database_path)
    events = store.load_position_transaction_events(
        position_address,
        event_type="Rebalancing",
    )
    if not events:
        raise ValueError(
            f"no decoded Rebalancing events for position {position_address}"
        )

    samples: list[RebalanceExecutionSample] = []
    for event in events:
        signature = str(event["signature"])
        instruction_index = int(event["parent_ix_index"])
        request = store.rebalance_request(signature, instruction_index)
        receipt = store.transaction_snapshot(signature)

        actual_active = int(event["active_bin_id"])
        actual_withdraw_x = int(str(event["x_withdrawn_amount"]))
        actual_deposit_x = int(str(event["x_added_amount"]))
        actual_withdraw_y = int(str(event["y_withdrawn_amount"]))
        actual_deposit_y = int(str(event["y_added_amount"]))

        if request is None:
            samples.append(
                RebalanceExecutionSample(
                    signature=signature,
                    instruction_index=instruction_index,
                    observed_active_id=None,
                    actual_active_id=actual_active,
                    active_bin_drift=None,
                    max_active_bin_slippage=None,
                    active_bin_guard_passed=None,
                    min_withdraw_x_amount=None,
                    actual_withdraw_x_amount=actual_withdraw_x,
                    withdraw_x_headroom=None,
                    withdraw_x_guard_passed=None,
                    max_deposit_x_amount=None,
                    actual_deposit_x_amount=actual_deposit_x,
                    deposit_x_headroom=None,
                    deposit_x_guard_passed=None,
                    min_withdraw_y_amount=None,
                    actual_withdraw_y_amount=actual_withdraw_y,
                    withdraw_y_headroom=None,
                    withdraw_y_guard_passed=None,
                    max_deposit_y_amount=None,
                    actual_deposit_y_amount=actual_deposit_y,
                    deposit_y_headroom=None,
                    deposit_y_guard_passed=None,
                    x_fee_amount=int(str(event["x_fee_amount"])),
                    y_fee_amount=int(str(event["y_fee_amount"])),
                    network_fee_lamports=(
                        int(receipt["network_fee_lamports"])
                        if receipt is not None
                        and receipt.get("network_fee_lamports") is not None
                        else None
                    ),
                    compute_units_consumed=(
                        int(receipt["compute_units_consumed"])
                        if receipt is not None
                        and receipt.get("compute_units_consumed") is not None
                        else None
                    ),
                    request_decoded=False,
                    all_guards_passed=None,
                )
            )
            continue

        observed_active = int(request["observed_active_id"])
        max_slippage = int(request["max_active_bin_slippage"])
        drift = actual_active - observed_active

        min_withdraw_x = int(str(request["min_withdraw_x_amount"]))
        max_deposit_x = int(str(request["max_deposit_x_amount"]))
        min_withdraw_y = int(str(request["min_withdraw_y_amount"]))
        max_deposit_y = int(str(request["max_deposit_y_amount"]))

        active_pass = abs(drift) <= max_slippage
        withdraw_x_pass = actual_withdraw_x >= min_withdraw_x
        deposit_x_pass = actual_deposit_x <= max_deposit_x
        withdraw_y_pass = actual_withdraw_y >= min_withdraw_y
        deposit_y_pass = actual_deposit_y <= max_deposit_y
        all_pass = all(
            (
                active_pass,
                withdraw_x_pass,
                deposit_x_pass,
                withdraw_y_pass,
                deposit_y_pass,
            )
        )

        samples.append(
            RebalanceExecutionSample(
                signature=signature,
                instruction_index=instruction_index,
                observed_active_id=observed_active,
                actual_active_id=actual_active,
                active_bin_drift=drift,
                max_active_bin_slippage=max_slippage,
                active_bin_guard_passed=active_pass,
                min_withdraw_x_amount=min_withdraw_x,
                actual_withdraw_x_amount=actual_withdraw_x,
                withdraw_x_headroom=actual_withdraw_x - min_withdraw_x,
                withdraw_x_guard_passed=withdraw_x_pass,
                max_deposit_x_amount=max_deposit_x,
                actual_deposit_x_amount=actual_deposit_x,
                deposit_x_headroom=max_deposit_x - actual_deposit_x,
                deposit_x_guard_passed=deposit_x_pass,
                min_withdraw_y_amount=min_withdraw_y,
                actual_withdraw_y_amount=actual_withdraw_y,
                withdraw_y_headroom=actual_withdraw_y - min_withdraw_y,
                withdraw_y_guard_passed=withdraw_y_pass,
                max_deposit_y_amount=max_deposit_y,
                actual_deposit_y_amount=actual_deposit_y,
                deposit_y_headroom=max_deposit_y - actual_deposit_y,
                deposit_y_guard_passed=deposit_y_pass,
                x_fee_amount=int(str(event["x_fee_amount"])),
                y_fee_amount=int(str(event["y_fee_amount"])),
                network_fee_lamports=(
                    int(receipt["network_fee_lamports"])
                    if receipt is not None
                    and receipt.get("network_fee_lamports") is not None
                    else None
                ),
                compute_units_consumed=(
                    int(receipt["compute_units_consumed"])
                    if receipt is not None
                    and receipt.get("compute_units_consumed") is not None
                    else None
                ),
                request_decoded=True,
                all_guards_passed=all_pass,
            )
        )

    decoded = [item for item in samples if item.request_decoded]
    fees = [
        item.network_fee_lamports
        for item in samples
        if item.network_fee_lamports is not None
    ]
    compute = [
        item.compute_units_consumed
        for item in samples
        if item.compute_units_consumed is not None
    ]
    return RebalanceExecutionCalibrationReport(
        position_address=position_address,
        rebalance_events=len(samples),
        request_decodes=len(decoded),
        guard_samples=len(decoded),
        guard_passes=sum(item.all_guards_passed is True for item in decoded),
        guard_violations=sum(item.all_guards_passed is False for item in decoded),
        request_coverage_rate=len(decoded) / len(samples),
        mean_network_fee_lamports=float(mean(fees)) if fees else None,
        mean_compute_units=float(mean(compute)) if compute else None,
        samples=tuple(samples),
    )
