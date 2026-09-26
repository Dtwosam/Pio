from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from .add_execution import build_add_execution_calibration
from .composition_reconciliation import build_composition_fee_reconciliation
from .rebalance_execution import build_rebalance_execution_calibration
from .research_store import ResearchStore
from .transaction_costs import build_transaction_cost_report


@dataclass(frozen=True)
class CalibrationGapCount:
    reason: str
    count: int


@dataclass(frozen=True)
class Phase2CalibrationEvidence:
    add_positions: int
    composition_add_events: int
    composition_eligible_samples: int
    composition_exact_samples: int
    composition_mismatched_samples: int
    composition_ineligible_samples: int
    composition_ineligibility_reasons: tuple[CalibrationGapCount, ...]
    add_execution_events: int
    add_execution_request_decodes: int
    add_execution_matched_events: int
    add_execution_unmatched_samples: int
    add_execution_gap_reasons: tuple[CalibrationGapCount, ...]
    add_active_guard_samples: int
    add_active_guard_violations: int
    rebalance_positions: int
    rebalance_events: int
    rebalance_request_decodes: int
    rebalance_guard_samples: int
    rebalance_guard_violations: int
    transaction_receipt_samples: int
    transaction_fee_samples: int
    missing_transaction_receipts: int
    evidence_gaps: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_phase2_calibration_evidence(
    database_path: str,
) -> Phase2CalibrationEvidence:
    store = ResearchStore(database_path)
    add_positions = store.position_history_addresses(event_type="add")
    rebalance_positions = store.transaction_event_position_addresses(
        event_type="Rebalancing",
    )

    composition_add_events = 0
    composition_eligible = 0
    composition_exact = 0
    composition_mismatched = 0
    composition_ineligible = 0
    composition_reasons: Counter[str] = Counter()
    add_execution_events = 0
    add_request_decodes = 0
    add_matched = 0
    add_gap_reasons: Counter[str] = Counter()
    add_guard_samples = 0
    add_guard_violations = 0

    receipt_by_signature: dict[str, tuple[bool, bool]] = {}
    missing_receipts: set[str] = set()

    for position in add_positions:
        try:
            composition = build_composition_fee_reconciliation(
                database_path,
                position_address=position,
            )
        except ValueError as exc:
            composition_reasons[f"composition report unavailable: {exc}"] += 1
        else:
            composition_add_events += composition.add_events
            composition_eligible += composition.eligible_samples
            composition_exact += composition.exact_samples
            composition_mismatched += composition.mismatched_samples
            for entry in composition.entries:
                if not entry.eligible:
                    composition_ineligible += 1
                    composition_reasons[
                        entry.reason or "composition sample ineligible"
                    ] += 1

        try:
            add_execution = build_add_execution_calibration(
                database_path,
                position_address=position,
            )
        except ValueError:
            pass
        else:
            add_execution_events += add_execution.add_events
            add_request_decodes += add_execution.request_decodes
            add_matched += add_execution.matched_add_events
            for sample in add_execution.samples:
                if not sample.request_decoded:
                    add_gap_reasons["add-liquidity request decode missing"] += 1
                elif not sample.add_event_found:
                    add_gap_reasons["AddLiquidity event decode missing"] += 1
            add_guard_samples += add_execution.guard_samples
            add_guard_violations += add_execution.guard_violations

        try:
            costs = build_transaction_cost_report(
                database_path,
                position_address=position,
            )
        except ValueError:
            continue
        for sample in costs.samples:
            if sample.network_fee_lamports is None and sample.compute_units_consumed is None:
                missing_receipts.add(sample.signature)
                continue
            receipt_by_signature[sample.signature] = (
                sample.network_fee_lamports is not None,
                sample.compute_units_consumed is not None,
            )

    rebalance_events = 0
    rebalance_request_decodes = 0
    rebalance_guard_samples = 0
    rebalance_guard_violations = 0
    for position in rebalance_positions:
        try:
            report = build_rebalance_execution_calibration(
                database_path,
                position_address=position,
            )
        except ValueError:
            continue
        rebalance_events += report.rebalance_events
        rebalance_request_decodes += report.request_decodes
        rebalance_guard_samples += report.guard_samples
        rebalance_guard_violations += report.guard_violations
        for sample in report.samples:
            if (
                sample.network_fee_lamports is None
                and sample.compute_units_consumed is None
            ):
                missing_receipts.add(sample.signature)
                continue
            receipt_by_signature[sample.signature] = (
                sample.network_fee_lamports is not None,
                sample.compute_units_consumed is not None,
            )

    gaps: list[str] = []
    if composition_eligible == 0:
        gaps.append("no exact composition-fee reconciliation samples")
    if composition_mismatched:
        gaps.append(
            f"{composition_mismatched} exact composition sample(s) mismatch"
        )
    if add_execution_events == 0:
        gaps.append("no add-liquidity execution calibration samples")
    elif add_request_decodes < add_execution_events:
        gaps.append(
            "add-liquidity request decode coverage is incomplete"
        )
    if add_guard_violations:
        gaps.append(f"{add_guard_violations} add active-bin guard violation(s)")
    if rebalance_guard_samples == 0:
        gaps.append("no decoded rebalance execution guard samples")
    if rebalance_guard_violations:
        gaps.append(
            f"{rebalance_guard_violations} rebalance execution guard violation(s)"
        )
    if not receipt_by_signature:
        gaps.append("no real Solana transaction receipt cost samples")
    if missing_receipts:
        gaps.append(
            f"{len(missing_receipts)} lifecycle transaction receipt(s) missing"
        )

    return Phase2CalibrationEvidence(
        add_positions=len(add_positions),
        composition_add_events=composition_add_events,
        composition_eligible_samples=composition_eligible,
        composition_exact_samples=composition_exact,
        composition_mismatched_samples=composition_mismatched,
        composition_ineligible_samples=composition_ineligible,
        composition_ineligibility_reasons=tuple(
            CalibrationGapCount(reason=reason, count=count)
            for reason, count in sorted(
                composition_reasons.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        add_execution_events=add_execution_events,
        add_execution_request_decodes=add_request_decodes,
        add_execution_matched_events=add_matched,
        add_execution_unmatched_samples=(
            add_execution_events - add_matched
        ),
        add_execution_gap_reasons=tuple(
            CalibrationGapCount(reason=reason, count=count)
            for reason, count in sorted(
                add_gap_reasons.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        add_active_guard_samples=add_guard_samples,
        add_active_guard_violations=add_guard_violations,
        rebalance_positions=len(rebalance_positions),
        rebalance_events=rebalance_events,
        rebalance_request_decodes=rebalance_request_decodes,
        rebalance_guard_samples=rebalance_guard_samples,
        rebalance_guard_violations=rebalance_guard_violations,
        transaction_receipt_samples=len(receipt_by_signature),
        transaction_fee_samples=sum(
            has_fee for has_fee, _ in receipt_by_signature.values()
        ),
        missing_transaction_receipts=len(missing_receipts),
        evidence_gaps=tuple(gaps),
    )
