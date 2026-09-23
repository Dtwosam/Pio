from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .composition_fee import simulate_active_bin_composition_fee
from .composition_prestate import (
    SUPPORTED_EXPLICIT_TYPES,
    SUPPORTED_STRATEGY_TYPES,
    SUPPORTED_STRATEGY_VARIANTS,
    build_composition_prestate_candidates,
)
from .deposit_plan import distribute_standard_spl_deposit
from .fee_state import deposit_total_fee_rate_at_timestamp
from .research_store import ResearchStore
from .strategy import StrategyType


@dataclass(frozen=True)
class CompositionFeeReconciliationSample:
    signature: str
    parent_ix_index: int
    snapshot_observed_at: str
    active_bin_id: int
    active_bin_amount_x: int
    active_bin_amount_y: int
    pre_bin_amount_x: int
    pre_bin_amount_y: int
    pre_liquidity_supply: int
    target_total_fee_rate: int
    protocol_share_bps: int
    predicted_fee_x: int
    actual_fee_x: int
    error_fee_x: int
    predicted_fee_y: int
    actual_fee_y: int
    error_fee_y: int
    predicted_protocol_fee_x: int
    actual_protocol_fee_x: int
    error_protocol_fee_x: int
    predicted_protocol_fee_y: int
    actual_protocol_fee_y: int
    error_protocol_fee_y: int
    exact_match: bool


@dataclass(frozen=True)
class CompositionFeeReconciliationEntry:
    signature: str
    parent_ix_index: int
    eligible: bool
    reason: str | None
    sample: CompositionFeeReconciliationSample | None


@dataclass(frozen=True)
class CompositionFeeReconciliationReport:
    position_address: str
    add_events: int
    eligible_samples: int
    exact_samples: int
    mismatched_samples: int
    exact_rate: float | None
    entries: tuple[CompositionFeeReconciliationEntry, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _active_bin_amounts(
    *,
    request: dict[str, Any],
    active_bin_id: int,
    bin_step: int,
    snapshot_bins: list[dict[str, Any]],
) -> tuple[int, int]:
    requested_x = int(str(request["requested_amount_x"]))
    requested_y = int(str(request["requested_amount_y"]))
    instruction_type = str(request["instruction_type"])

    if instruction_type in SUPPORTED_EXPLICIT_TYPES:
        distribution = json.loads(
            str(request.get("explicit_distribution_json") or "[]")
        )
        matches = [
            item
            for item in distribution
            if int(item["bin_id"]) == active_bin_id
        ]
        if len(matches) > 1:
            raise ValueError("explicit distribution contains duplicate active-bin rows")
        if not matches:
            return 0, 0
        item = matches[0]
        return (
            requested_x * int(item["distribution_x"]) // 10_000,
            requested_y * int(item["distribution_y"]) // 10_000,
        )

    if instruction_type in SUPPORTED_STRATEGY_TYPES:
        variant = int(request["strategy_variant"])
        strategy_name = SUPPORTED_STRATEGY_VARIANTS.get(variant)
        if strategy_name is None:
            raise ValueError("unsupported strategy variant")
        prices = {
            int(row["bin_id"]): int(str(row["price"]))
            for row in snapshot_bins
        }
        plan = distribute_standard_spl_deposit(
            active_id=active_bin_id,
            min_bin_id=int(request["min_bin_id"]),
            max_bin_id=int(request["max_bin_id"]),
            amount_x=requested_x,
            amount_y=requested_y,
            strategy=StrategyType(strategy_name),
            prices_q64=prices,
            favor_x_in_active_bin=bool(request["strategy_favor_x"]),
        )
        item = next(
            (bin_row for bin_row in plan.bins if bin_row.bin_id == active_bin_id),
            None,
        )
        if item is None:
            return 0, 0
        return item.amount_x, item.amount_y

    raise ValueError(f"unsupported allocation instruction: {instruction_type}")


def _verification_reason(
    verification: dict[str, Any] | None,
    *,
    candidate_addresses: tuple[str, ...],
    transaction_slot: int,
    capture_slot_start: int,
    capture_slot_end: int,
) -> str | None:
    if verification is None:
        return "exact prestate verification has not been ingested"
    if not bool(verification["eligible"]):
        return "exact prestate verifier rejected the snapshot"
    if int(verification["transaction_slot"]) != transaction_slot:
        return "prestate verification transaction slot does not match candidate"
    if int(verification["capture_slot_start"]) != capture_slot_start:
        return "prestate verification capture start slot does not match candidate"
    if int(verification["capture_slot_end"]) != capture_slot_end:
        return "prestate verification capture end slot does not match candidate"

    checks = json.loads(str(verification["account_checks_json"]))
    by_address = {
        str(item.get("address")): item
        for item in checks
        if isinstance(item, dict)
    }
    for address in candidate_addresses:
        check = by_address.get(address)
        if check is None:
            return f"prestate verification is missing account check for {address}"
        if not bool(check.get("eligible")):
            return f"prestate verification account check rejected {address}"
    return None


def build_composition_fee_reconciliation(
    database_path: str,
    *,
    position_address: str,
) -> CompositionFeeReconciliationReport:
    store = ResearchStore(database_path)
    candidate_report = build_composition_prestate_candidates(
        database_path,
        position_address=position_address,
    )
    entries: list[CompositionFeeReconciliationEntry] = []

    for candidate in candidate_report.candidates:
        if not candidate.eligible_for_verification:
            entries.append(
                CompositionFeeReconciliationEntry(
                    signature=candidate.signature,
                    parent_ix_index=candidate.parent_ix_index,
                    eligible=False,
                    reason=candidate.ineligibility_reason,
                    sample=None,
                )
            )
            continue

        assert candidate.transaction_slot is not None
        assert candidate.transaction_block_time is not None
        assert candidate.active_bin_id is not None
        assert candidate.snapshot_observed_at is not None
        assert candidate.capture_slot_start is not None
        assert candidate.capture_slot_end is not None

        verification = store.prestate_verification(
            candidate.signature,
            snapshot_observed_at=candidate.snapshot_observed_at,
        )
        reason = _verification_reason(
            verification,
            candidate_addresses=candidate.verification_addresses,
            transaction_slot=candidate.transaction_slot,
            capture_slot_start=candidate.capture_slot_start,
            capture_slot_end=candidate.capture_slot_end,
        )
        if reason is not None:
            entries.append(
                CompositionFeeReconciliationEntry(
                    signature=candidate.signature,
                    parent_ix_index=candidate.parent_ix_index,
                    eligible=False,
                    reason=reason,
                    sample=None,
                )
            )
            continue

        capture = store.pool_capture_before_slot(
            candidate.pool_address,
            target_slot=candidate.transaction_slot,
            active_bin_id=candidate.active_bin_id,
        )
        if capture is None or str(capture["observed_at"]) != candidate.snapshot_observed_at:
            raise ValueError("verified prestate candidate changed during reconciliation")

        bin_row = store.bin_liquidity_at(
            candidate.pool_address,
            observed_at=candidate.snapshot_observed_at,
            bin_id=candidate.active_bin_id,
        )
        if bin_row is None:
            raise ValueError("verified prestate active bin is missing")

        request = store.add_liquidity_request(
            candidate.signature,
            candidate.parent_ix_index,
        )
        if request is None:
            raise ValueError("verified add request disappeared")

        snapshot_bins = store.load_bin_liquidity(
            candidate.pool_address,
            observed_at=candidate.snapshot_observed_at,
        )
        try:
            active_amount_x, active_amount_y = _active_bin_amounts(
                request=request,
                active_bin_id=candidate.active_bin_id,
                bin_step=int(capture["bin_step"]),
                snapshot_bins=snapshot_bins,
            )
        except ValueError as exc:
            entries.append(
                CompositionFeeReconciliationEntry(
                    signature=candidate.signature,
                    parent_ix_index=candidate.parent_ix_index,
                    eligible=False,
                    reason=str(exc),
                    sample=None,
                )
            )
            continue

        target_fee_rate = deposit_total_fee_rate_at_timestamp(
            bin_step=int(capture["bin_step"]),
            active_bin_id=candidate.active_bin_id,
            target_timestamp=candidate.transaction_block_time,
            fee_state=capture,
        )
        protocol_share_bps = int(capture["protocol_share_bps"])
        predicted = simulate_active_bin_composition_fee(
            amount_x=active_amount_x,
            amount_y=active_amount_y,
            price_q64=int(str(bin_row["price"])),
            bin_amount_x=int(str(bin_row["amount_x"])),
            bin_amount_y=int(str(bin_row["amount_y"])),
            liquidity_supply=int(str(bin_row["liquidity_supply"])),
            total_fee_rate=target_fee_rate,
            protocol_share_bps=protocol_share_bps,
        )

        chain_events = store.load_transaction_events(
            candidate.signature,
            parent_ix_index=candidate.parent_ix_index,
        )
        composition_events = [
            item
            for item in chain_events
            if item["event_type"] == "CompositionFee"
            and int(item["bin_id"]) == candidate.active_bin_id
        ]
        actual_fee_x = sum(
            int(str(item["token_x_fee_amount"]))
            for item in composition_events
        )
        actual_fee_y = sum(
            int(str(item["token_y_fee_amount"]))
            for item in composition_events
        )
        actual_protocol_x = sum(
            int(str(item["protocol_token_x_fee_amount"]))
            for item in composition_events
        )
        actual_protocol_y = sum(
            int(str(item["protocol_token_y_fee_amount"]))
            for item in composition_events
        )

        if not composition_events or actual_fee_x + actual_fee_y <= 0:
            entries.append(
                CompositionFeeReconciliationEntry(
                    signature=candidate.signature,
                    parent_ix_index=candidate.parent_ix_index,
                    eligible=False,
                    reason="no positive CompositionFee event for formula validation",
                    sample=None,
                )
            )
            continue

        errors = (
            predicted.composition_fee_x - actual_fee_x,
            predicted.composition_fee_y - actual_fee_y,
            predicted.protocol_fee_x - actual_protocol_x,
            predicted.protocol_fee_y - actual_protocol_y,
        )
        exact = all(value == 0 for value in errors)

        sample = CompositionFeeReconciliationSample(
            signature=candidate.signature,
            parent_ix_index=candidate.parent_ix_index,
            snapshot_observed_at=candidate.snapshot_observed_at,
            active_bin_id=candidate.active_bin_id,
            active_bin_amount_x=active_amount_x,
            active_bin_amount_y=active_amount_y,
            pre_bin_amount_x=int(str(bin_row["amount_x"])),
            pre_bin_amount_y=int(str(bin_row["amount_y"])),
            pre_liquidity_supply=int(str(bin_row["liquidity_supply"])),
            target_total_fee_rate=target_fee_rate,
            protocol_share_bps=protocol_share_bps,
            predicted_fee_x=predicted.composition_fee_x,
            actual_fee_x=actual_fee_x,
            error_fee_x=errors[0],
            predicted_fee_y=predicted.composition_fee_y,
            actual_fee_y=actual_fee_y,
            error_fee_y=errors[1],
            predicted_protocol_fee_x=predicted.protocol_fee_x,
            actual_protocol_fee_x=actual_protocol_x,
            error_protocol_fee_x=errors[2],
            predicted_protocol_fee_y=predicted.protocol_fee_y,
            actual_protocol_fee_y=actual_protocol_y,
            error_protocol_fee_y=errors[3],
            exact_match=exact,
        )
        entries.append(
            CompositionFeeReconciliationEntry(
                signature=candidate.signature,
                parent_ix_index=candidate.parent_ix_index,
                eligible=True,
                reason=None,
                sample=sample,
            )
        )

    eligible = [item for item in entries if item.eligible and item.sample is not None]
    exact = sum(item.sample.exact_match for item in eligible if item.sample is not None)
    return CompositionFeeReconciliationReport(
        position_address=position_address,
        add_events=len(entries),
        eligible_samples=len(eligible),
        exact_samples=exact,
        mismatched_samples=len(eligible) - exact,
        exact_rate=exact / len(eligible) if eligible else None,
        entries=tuple(entries),
    )
