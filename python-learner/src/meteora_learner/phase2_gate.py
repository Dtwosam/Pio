from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .reconciliation_corpus import (
    ReconciliationCorpusReport,
    build_reconciliation_corpus,
)


@dataclass(frozen=True)
class Phase2CapabilityStatus:
    position_amount_reconciliation: bool = True
    fee_checkpoint_reconciliation: bool = True
    composition_event_labels: bool = True
    composition_formula_reconciliation: bool = False
    rebalance_lifecycle: bool = False
    reward_accounting: bool = True


CURRENT_PHASE2_CAPABILITIES = Phase2CapabilityStatus()


@dataclass(frozen=True)
class Phase2PromotionCriteria:
    min_positions: int
    min_amount_bins: int
    min_fee_intervals: int
    min_fee_bins: int
    min_reward_intervals: int
    min_reward_growth_bins: int
    min_amount_coverage_rate: float = 1.0

    def __post_init__(self) -> None:
        if self.min_positions <= 0:
            raise ValueError("min_positions must be positive")
        if self.min_amount_bins <= 0:
            raise ValueError("min_amount_bins must be positive")
        if self.min_fee_intervals <= 0:
            raise ValueError("min_fee_intervals must be positive")
        if self.min_fee_bins <= 0:
            raise ValueError("min_fee_bins must be positive")
        if self.min_reward_intervals <= 0:
            raise ValueError("min_reward_intervals must be positive")
        if self.min_reward_growth_bins <= 0:
            raise ValueError("min_reward_growth_bins must be positive")
        if not 0.0 <= self.min_amount_coverage_rate <= 1.0:
            raise ValueError("min_amount_coverage_rate must be between 0 and 1")


@dataclass(frozen=True)
class Phase2PromotionGate:
    criteria: Phase2PromotionCriteria
    corpus: ReconciliationCorpusReport
    amount_coverage_rate: float
    exact_math_passed: bool
    sample_sufficiency_passed: bool
    capability_gate_passed: bool
    capabilities: Phase2CapabilityStatus
    promotion_ready: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase2_promotion_gate(
    database_path: str,
    *,
    criteria: Phase2PromotionCriteria,
    position_limit: int | None = None,
    capabilities: Phase2CapabilityStatus = CURRENT_PHASE2_CAPABILITIES,
) -> Phase2PromotionGate:
    corpus = build_reconciliation_corpus(
        database_path,
        position_limit=position_limit,
    )

    amount_coverage_rate = (
        corpus.amount_positions_eligible / corpus.positions_seen
        if corpus.positions_seen
        else 0.0
    )

    reasons: list[str] = []

    exact_math_passed = corpus.strict_math_gate_passed
    if not exact_math_passed:
        if corpus.amount_positions_eligible == 0:
            reasons.append("no eligible amount-state reconciliation samples")
        if corpus.fee_intervals_eligible == 0:
            reasons.append("no eligible fee reconciliation intervals")
        if corpus.reward_intervals_eligible == 0:
            reasons.append("no eligible reward reconciliation intervals")
        if corpus.reward_bins_with_checkpoint_growth == 0:
            reasons.append("no reward bins with checkpoint growth")
        if corpus.amount_mismatched_bins:
            reasons.append(
                f"{corpus.amount_mismatched_bins} amount-state bins do not match exactly"
            )
        if corpus.fee_mismatched_bins:
            reasons.append(
                f"{corpus.fee_mismatched_bins} fee bins do not match exactly"
            )
        if corpus.reward_mismatched_bins:
            reasons.append(
                f"{corpus.reward_mismatched_bins} reward bins do not match exactly"
            )

    sample_checks = [
        (
            corpus.positions_seen >= criteria.min_positions,
            f"positions_seen {corpus.positions_seen} < required {criteria.min_positions}",
        ),
        (
            corpus.amount_bins_checked >= criteria.min_amount_bins,
            f"amount_bins_checked {corpus.amount_bins_checked} < required "
            f"{criteria.min_amount_bins}",
        ),
        (
            corpus.fee_intervals_eligible >= criteria.min_fee_intervals,
            f"fee_intervals_eligible {corpus.fee_intervals_eligible} < required "
            f"{criteria.min_fee_intervals}",
        ),
        (
            corpus.fee_bins_checked >= criteria.min_fee_bins,
            f"fee_bins_checked {corpus.fee_bins_checked} < required "
            f"{criteria.min_fee_bins}",
        ),
        (
            corpus.reward_intervals_eligible >= criteria.min_reward_intervals,
            f"reward_intervals_eligible {corpus.reward_intervals_eligible} < required "
            f"{criteria.min_reward_intervals}",
        ),
        (
            corpus.reward_bins_with_checkpoint_growth >= criteria.min_reward_growth_bins,
            f"reward_bins_with_checkpoint_growth "
            f"{corpus.reward_bins_with_checkpoint_growth} < required "
            f"{criteria.min_reward_growth_bins}",
        ),
        (
            amount_coverage_rate >= criteria.min_amount_coverage_rate,
            f"amount_coverage_rate {amount_coverage_rate:.6f} < required "
            f"{criteria.min_amount_coverage_rate:.6f}",
        ),
    ]
    sample_sufficiency_passed = all(ok for ok, _ in sample_checks)
    reasons.extend(message for ok, message in sample_checks if not ok)

    required_capabilities = {
        "position_amount_reconciliation": capabilities.position_amount_reconciliation,
        "fee_checkpoint_reconciliation": capabilities.fee_checkpoint_reconciliation,
        "composition_formula_reconciliation": capabilities.composition_formula_reconciliation,
        "rebalance_lifecycle": capabilities.rebalance_lifecycle,
        "reward_accounting": capabilities.reward_accounting,
    }
    capability_gate_passed = all(required_capabilities.values())
    for name, ready in required_capabilities.items():
        if not ready:
            reasons.append(f"required capability not validated: {name}")

    promotion_ready = (
        exact_math_passed
        and sample_sufficiency_passed
        and capability_gate_passed
    )

    return Phase2PromotionGate(
        criteria=criteria,
        corpus=corpus,
        amount_coverage_rate=amount_coverage_rate,
        exact_math_passed=exact_math_passed,
        sample_sufficiency_passed=sample_sufficiency_passed,
        capability_gate_passed=capability_gate_passed,
        capabilities=capabilities,
        promotion_ready=promotion_ready,
        reasons=tuple(reasons),
    )
