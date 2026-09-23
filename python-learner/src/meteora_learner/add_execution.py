from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import mean, median
from typing import Any

from .research_store import ResearchStore


@dataclass(frozen=True)
class AddExecutionSample:
    signature: str
    instruction_index: int
    instruction_type: str | None
    requested_amount_x: int | None
    requested_amount_y: int | None
    actual_amount_x: int | None
    actual_amount_y: int | None
    unused_amount_x: int | None
    unused_amount_y: int | None
    underfill_bps_x: int | None
    underfill_bps_y: int | None
    observed_active_id: int | None
    actual_active_id: int | None
    active_bin_drift: int | None
    max_active_bin_slippage: int | None
    active_bin_guard_passed: bool | None
    request_decoded: bool
    add_event_found: bool


@dataclass(frozen=True)
class AddExecutionCalibrationReport:
    position_address: str
    add_events: int
    request_decodes: int
    matched_add_events: int
    guard_samples: int
    guard_violations: int
    amount_x_samples: int
    amount_y_samples: int
    mean_underfill_bps_x: float | None
    median_underfill_bps_x: float | None
    p95_underfill_bps_x: int | None
    mean_underfill_bps_y: float | None
    median_underfill_bps_y: float | None
    p95_underfill_bps_y: int | None
    decode_coverage_rate: float
    event_match_rate: float
    samples: tuple[AddExecutionSample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _underfill_bps(requested: int, actual: int) -> int | None:
    if requested <= 0:
        return None
    return (requested - actual) * 10_000 // requested


def build_add_execution_calibration(
    database_path: str,
    *,
    position_address: str,
) -> AddExecutionCalibrationReport:
    """
    Compare requested add-liquidity bounds with actual AddLiquidity events.

    Negative underfill means actual event amount exceeded the decoded request and
    remains visible as a calibration anomaly. Strategy/weight variants also
    validate actual active-bin drift against max_active_bin_slippage.
    """
    store = ResearchStore(database_path)
    adds = store.load_position_history_events(position_address, event_type="add")
    if not adds:
        raise ValueError(f"no stored add events for position {position_address}")

    samples: list[AddExecutionSample] = []
    for row in adds:
        signature = str(row["signature"])
        instruction_index = int(row["ix_index"])
        request = store.add_liquidity_request(signature, instruction_index)
        events = store.load_transaction_events(
            signature,
            parent_ix_index=instruction_index,
        )
        add_event = next(
            (
                item
                for item in events
                if item["event_type"] == "AddLiquidity"
                and item["position_address"] == position_address
            ),
            None,
        )

        requested_x = (
            int(str(request["requested_amount_x"]))
            if request is not None
            else None
        )
        requested_y = (
            int(str(request["requested_amount_y"]))
            if request is not None
            else None
        )
        actual_x = (
            int(str(add_event["amount_x"]))
            if add_event is not None
            else None
        )
        actual_y = (
            int(str(add_event["amount_y"]))
            if add_event is not None
            else None
        )

        unused_x = (
            requested_x - actual_x
            if requested_x is not None and actual_x is not None
            else None
        )
        unused_y = (
            requested_y - actual_y
            if requested_y is not None and actual_y is not None
            else None
        )
        observed_active = (
            int(request["observed_active_id"])
            if request is not None and request.get("observed_active_id") is not None
            else None
        )
        actual_active = (
            int(add_event["active_bin_id"])
            if add_event is not None and add_event.get("active_bin_id") is not None
            else None
        )
        max_slippage = (
            int(request["max_active_bin_slippage"])
            if request is not None
            and request.get("max_active_bin_slippage") is not None
            else None
        )
        drift = (
            actual_active - observed_active
            if actual_active is not None and observed_active is not None
            else None
        )
        guard_passed = (
            abs(drift) <= max_slippage
            if drift is not None and max_slippage is not None
            else None
        )

        samples.append(
            AddExecutionSample(
                signature=signature,
                instruction_index=instruction_index,
                instruction_type=(
                    str(request["instruction_type"])
                    if request is not None
                    else None
                ),
                requested_amount_x=requested_x,
                requested_amount_y=requested_y,
                actual_amount_x=actual_x,
                actual_amount_y=actual_y,
                unused_amount_x=unused_x,
                unused_amount_y=unused_y,
                underfill_bps_x=(
                    _underfill_bps(requested_x, actual_x)
                    if requested_x is not None and actual_x is not None
                    else None
                ),
                underfill_bps_y=(
                    _underfill_bps(requested_y, actual_y)
                    if requested_y is not None and actual_y is not None
                    else None
                ),
                observed_active_id=observed_active,
                actual_active_id=actual_active,
                active_bin_drift=drift,
                max_active_bin_slippage=max_slippage,
                active_bin_guard_passed=guard_passed,
                request_decoded=request is not None,
                add_event_found=add_event is not None,
            )
        )

    x_bps = [item.underfill_bps_x for item in samples if item.underfill_bps_x is not None]
    y_bps = [item.underfill_bps_y for item in samples if item.underfill_bps_y is not None]
    guard = [item for item in samples if item.active_bin_guard_passed is not None]
    request_decodes = sum(item.request_decoded for item in samples)
    matched = sum(item.request_decoded and item.add_event_found for item in samples)

    return AddExecutionCalibrationReport(
        position_address=position_address,
        add_events=len(samples),
        request_decodes=request_decodes,
        matched_add_events=matched,
        guard_samples=len(guard),
        guard_violations=sum(item.active_bin_guard_passed is False for item in guard),
        amount_x_samples=len(x_bps),
        amount_y_samples=len(y_bps),
        mean_underfill_bps_x=float(mean(x_bps)) if x_bps else None,
        median_underfill_bps_x=float(median(x_bps)) if x_bps else None,
        p95_underfill_bps_x=_p95(x_bps),
        mean_underfill_bps_y=float(mean(y_bps)) if y_bps else None,
        median_underfill_bps_y=float(median(y_bps)) if y_bps else None,
        p95_underfill_bps_y=_p95(y_bps),
        decode_coverage_rate=request_decodes / len(samples),
        event_match_rate=matched / len(samples),
        samples=tuple(samples),
    )
