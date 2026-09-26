from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Sequence

from .ml_dataset import MLTrainingExample


@dataclass(frozen=True)
class PaperCandidateArm:
    strategy: str
    half_width: int
    center_offset: int

    @property
    def key(self) -> str:
        return (
            f"{self.strategy}|w={self.half_width}"
            f"|o={self.center_offset}"
        )


@dataclass(frozen=True)
class PaperCandidateEvidence:
    arm: PaperCandidateArm
    observations: int
    mean_excess_vs_hold_bps: float | None
    mean_net_return_bps: float | None
    mean_range_survival_ratio: float | None


@dataclass(frozen=True)
class EmpiricalPaperCandidateSelection:
    pool_address: str
    context_key: str
    status: str
    selection_mode: str
    selected_arm: PaperCandidateArm | None
    paper_only: bool
    live_authorized: bool
    evidence: tuple[PaperCandidateEvidence, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def candidate_context_key(
    *,
    active_bin_move_1: int,
    below_active_liquidity_ratio: float,
    above_active_liquidity_ratio: float,
    fee_growth_bins_x: int,
    fee_growth_bins_y: int,
) -> str:
    if active_bin_move_1 > 0:
        move = "UP"
    elif active_bin_move_1 < 0:
        move = "DOWN"
    else:
        move = "FLAT"

    skew = above_active_liquidity_ratio - below_active_liquidity_ratio
    if skew > 0.10:
        liquidity = "ABOVE"
    elif skew < -0.10:
        liquidity = "BELOW"
    else:
        liquidity = "BALANCED"

    fee_activity = (
        "ACTIVE"
        if fee_growth_bins_x + fee_growth_bins_y > 0
        else "QUIET"
    )
    return f"{move}|{liquidity}|{fee_activity}"


def _example_context(example: MLTrainingExample) -> str:
    return candidate_context_key(
        active_bin_move_1=example.active_bin_move_1,
        below_active_liquidity_ratio=example.below_active_liquidity_ratio,
        above_active_liquidity_ratio=example.above_active_liquidity_ratio,
        fee_growth_bins_x=example.fee_growth_bins_x,
        fee_growth_bins_y=example.fee_growth_bins_y,
    )


def _example_arm(example: MLTrainingExample) -> PaperCandidateArm:
    return PaperCandidateArm(
        strategy=example.strategy,
        half_width=example.half_width,
        center_offset=example.center_offset,
    )


def select_empirical_paper_candidate(
    *,
    historical_examples: Sequence[MLTrainingExample],
    pool_address: str,
    context_key: str,
    current_candidates: Sequence[PaperCandidateArm],
) -> EmpiricalPaperCandidateSelection:
    """
    Select a PAPER-only candidate from observed forward labels.

    There are deliberately no economic cutoffs here. For the same pool and
    observed context, unseen arms are explored in PAPER first. Once every
    current arm has evidence, the arm with the highest observed mean excess
    versus hold is selected. Nothing returned by this function authorizes live
    capital movement.
    """
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if not context_key.strip():
        raise ValueError("context_key is required")
    if not current_candidates:
        raise ValueError("at least one current candidate is required")

    by_key: dict[str, PaperCandidateArm] = {}
    for candidate in current_candidates:
        if candidate.half_width < 0:
            raise ValueError("candidate half_width cannot be negative")
        if not candidate.strategy.strip():
            raise ValueError("candidate strategy is required")
        if candidate.key in by_key:
            raise ValueError(f"duplicate current candidate: {candidate.key}")
        by_key[candidate.key] = candidate

    matching = [
        item
        for item in historical_examples
        if item.pool_address == pool_address
        and _example_context(item) == context_key
        and _example_arm(item).key in by_key
    ]

    grouped: dict[str, list[MLTrainingExample]] = {
        key: [] for key in by_key
    }
    for item in matching:
        grouped[_example_arm(item).key].append(item)

    evidence: list[PaperCandidateEvidence] = []
    for key in sorted(by_key):
        rows = grouped[key]
        evidence.append(
            PaperCandidateEvidence(
                arm=by_key[key],
                observations=len(rows),
                mean_excess_vs_hold_bps=(
                    float(mean(item.target_excess_vs_hold_bps for item in rows))
                    if rows
                    else None
                ),
                mean_net_return_bps=(
                    float(mean(item.target_net_return_bps for item in rows))
                    if rows
                    else None
                ),
                mean_range_survival_ratio=(
                    float(mean(item.target_range_survival_ratio for item in rows))
                    if rows
                    else None
                ),
            )
        )

    if not matching:
        return EmpiricalPaperCandidateSelection(
            pool_address=pool_address,
            context_key=context_key,
            status="INSUFFICIENT_CONTEXT_EVIDENCE",
            selection_mode="NO_SELECTION",
            selected_arm=None,
            paper_only=True,
            live_authorized=False,
            evidence=tuple(evidence),
        )

    unseen = [item for item in evidence if item.observations == 0]
    if unseen:
        chosen = sorted(unseen, key=lambda item: item.arm.key)[0]
        return EmpiricalPaperCandidateSelection(
            pool_address=pool_address,
            context_key=context_key,
            status="PAPER_EXPLORATION",
            selection_mode="UNSEEN_ARM",
            selected_arm=chosen.arm,
            paper_only=True,
            live_authorized=False,
            evidence=tuple(evidence),
        )

    chosen = sorted(
        evidence,
        key=lambda item: (
            -float(item.mean_excess_vs_hold_bps),
            -item.observations,
            item.arm.key,
        ),
    )[0]
    return EmpiricalPaperCandidateSelection(
        pool_address=pool_address,
        context_key=context_key,
        status="PAPER_EMPIRICAL_SELECTION",
        selection_mode="MEAN_OBSERVED_EXCESS_VS_HOLD",
        selected_arm=chosen.arm,
        paper_only=True,
        live_authorized=False,
        evidence=tuple(evidence),
    )
