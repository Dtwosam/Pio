from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .chain_scan import ChainCandidateOutcome, ChainScanResult
from .liquidity_math import Q64
from .phase2_gate import Phase2PromotionGate
from .research_store import ResearchStore


@dataclass(frozen=True)
class BaselinePolicyConfig:
    min_range_survival_ratio: float = 0.75
    min_excess_vs_hold_bps: int = 0
    require_fee_cost_recovery: bool = True
    estimated_network_cost_y_atomic: int | None = None
    reject_unvalued_rewards: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_range_survival_ratio <= 1.0:
            raise ValueError("min_range_survival_ratio must be between 0 and 1")
        if self.estimated_network_cost_y_atomic is not None:
            if self.estimated_network_cost_y_atomic < 0:
                raise ValueError("estimated_network_cost_y_atomic cannot be negative")


@dataclass(frozen=True)
class ReplayEconomicMetrics:
    entry_price_q64: int
    exit_price_q64: int
    initial_x: int
    initial_y: int
    hold_value_y_atomic: int
    lp_inventory_value_y_atomic: int
    fee_value_y_atomic: int
    composition_cost_value_y_atomic: int
    network_cost_y_atomic: int | None
    adjusted_lp_value_y_atomic: int | None
    excess_vs_hold_y_atomic: int | None
    excess_vs_hold_bps: int | None
    fee_minus_entry_cost_y_atomic: int | None
    has_unvalued_rewards: bool


@dataclass(frozen=True)
class BaselineCandidateAssessment:
    strategy: str
    half_width: int
    center_offset: int
    min_bin_id: int
    max_bin_id: int
    eligible: bool
    rejection_reasons: tuple[str, ...]
    range_survival_ratio: float | None
    max_observed_share_bps: int | None
    economics: ReplayEconomicMetrics | None


@dataclass(frozen=True)
class BaselineSelection:
    pool_address: str
    phase2_ready: bool
    phase2_blockers: tuple[str, ...]
    candidates_evaluated: int
    candidates_eligible: int
    research_choice: BaselineCandidateAssessment | None
    actionable_choice: BaselineCandidateAssessment | None
    selection_rule: str
    assessments: tuple[BaselineCandidateAssessment, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def q64_value_in_y_atomic(*, amount_x: int, amount_y: int, price_q64: int) -> int:
    if amount_x < 0 or amount_y < 0:
        raise ValueError("amounts cannot be negative")
    if price_q64 <= 0:
        raise ValueError("price_q64 must be positive")
    return amount_y + (amount_x * price_q64 // Q64)


def replay_economics(
    candidate: ChainCandidateOutcome,
    *,
    entry_price_q64: int,
    exit_price_q64: int,
    network_cost_y_atomic: int | None,
) -> ReplayEconomicMetrics:
    replay = candidate.replay
    if replay is None:
        raise ValueError("accepted candidate replay is required")

    initial_x = replay.deposited_x + replay.idle_x
    initial_y = replay.deposited_y + replay.idle_y
    hold_value = q64_value_in_y_atomic(
        amount_x=initial_x,
        amount_y=initial_y,
        price_q64=exit_price_q64,
    )

    lp_inventory_value = q64_value_in_y_atomic(
        amount_x=replay.ending_x + replay.idle_x,
        amount_y=replay.ending_y + replay.idle_y,
        price_q64=exit_price_q64,
    )
    fee_value = q64_value_in_y_atomic(
        amount_x=replay.fee_x,
        amount_y=replay.fee_y,
        price_q64=exit_price_q64,
    )
    composition_cost = q64_value_in_y_atomic(
        amount_x=replay.entry_composition_fee_x,
        amount_y=replay.entry_composition_fee_y,
        price_q64=exit_price_q64,
    )
    has_unvalued_rewards = replay.reward_one != 0 or replay.reward_two != 0

    adjusted_value: int | None = None
    excess: int | None = None
    excess_bps: int | None = None
    fee_minus_cost: int | None = None
    if network_cost_y_atomic is not None:
        adjusted_value = (
            lp_inventory_value
            + fee_value
            - composition_cost
            - network_cost_y_atomic
        )
        excess = adjusted_value - hold_value
        excess_bps = (
            excess * 10_000 // hold_value
            if hold_value > 0
            else None
        )
        fee_minus_cost = fee_value - composition_cost - network_cost_y_atomic

    return ReplayEconomicMetrics(
        entry_price_q64=entry_price_q64,
        exit_price_q64=exit_price_q64,
        initial_x=initial_x,
        initial_y=initial_y,
        hold_value_y_atomic=hold_value,
        lp_inventory_value_y_atomic=lp_inventory_value,
        fee_value_y_atomic=fee_value,
        composition_cost_value_y_atomic=composition_cost,
        network_cost_y_atomic=network_cost_y_atomic,
        adjusted_lp_value_y_atomic=adjusted_value,
        excess_vs_hold_y_atomic=excess,
        excess_vs_hold_bps=excess_bps,
        fee_minus_entry_cost_y_atomic=fee_minus_cost,
        has_unvalued_rewards=has_unvalued_rewards,
    )


def _active_price(
    store: ResearchStore,
    *,
    pool_address: str,
    observed_at: str,
    active_bin_id: int,
) -> int:
    row = store.bin_liquidity_at(
        pool_address,
        observed_at=observed_at,
        bin_id=active_bin_id,
    )
    if row is None:
        raise ValueError(
            f"active-bin price missing at {observed_at} for bin {active_bin_id}"
        )
    price = int(str(row["price"]))
    if price <= 0:
        raise ValueError("active-bin Q64 price must be positive")
    return price


def _rank_key(item: BaselineCandidateAssessment) -> tuple[object, ...]:
    assert item.economics is not None
    assert item.economics.excess_vs_hold_bps is not None
    assert item.economics.fee_minus_entry_cost_y_atomic is not None
    assert item.range_survival_ratio is not None
    assert item.max_observed_share_bps is not None
    return (
        item.economics.excess_vs_hold_bps,
        item.range_survival_ratio,
        item.economics.fee_minus_entry_cost_y_atomic,
        -item.max_observed_share_bps,
        -item.half_width,
        -abs(item.center_offset),
        item.strategy,
    )


def select_deterministic_baseline(
    database_path: str,
    *,
    scan: ChainScanResult,
    phase2_gate: Phase2PromotionGate,
    config: BaselinePolicyConfig = BaselinePolicyConfig(),
) -> BaselineSelection:
    """
    Select one trailing-history research candidate with explicit deterministic rules.

    This is not a forecast. It summarizes which candidate best satisfied the
    configured rules over the already-observed scan window. It becomes actionable
    only when the supplied Phase 2 promotion gate is ready.
    """
    store = ResearchStore(database_path)
    assessments: list[BaselineCandidateAssessment] = []

    for candidate in scan.candidates:
        if candidate.status != "ACCEPTED" or candidate.replay is None:
            assessments.append(
                BaselineCandidateAssessment(
                    strategy=candidate.strategy,
                    half_width=candidate.half_width,
                    center_offset=candidate.center_offset,
                    min_bin_id=candidate.min_bin_id,
                    max_bin_id=candidate.max_bin_id,
                    eligible=False,
                    rejection_reasons=(
                        candidate.rejection_reason or "chain replay rejected candidate",
                    ),
                    range_survival_ratio=candidate.range_survival_ratio,
                    max_observed_share_bps=None,
                    economics=None,
                )
            )
            continue

        replay = candidate.replay
        reasons: list[str] = []
        try:
            entry_price = _active_price(
                store,
                pool_address=scan.pool_address,
                observed_at=replay.start_observed_at,
                active_bin_id=replay.start_active_bin_id,
            )
            exit_price = _active_price(
                store,
                pool_address=scan.pool_address,
                observed_at=replay.end_observed_at,
                active_bin_id=replay.end_active_bin_id,
            )
            economics = replay_economics(
                candidate,
                entry_price_q64=entry_price,
                exit_price_q64=exit_price,
                network_cost_y_atomic=config.estimated_network_cost_y_atomic,
            )
        except ValueError as exc:
            economics = None
            reasons.append(str(exc))

        if candidate.range_survival_ratio is None:
            reasons.append("range survival is unavailable")
        elif candidate.range_survival_ratio < config.min_range_survival_ratio:
            reasons.append(
                f"range survival {candidate.range_survival_ratio:.6f} < required "
                f"{config.min_range_survival_ratio:.6f}"
            )

        if economics is not None:
            if (
                config.reject_unvalued_rewards
                and economics.has_unvalued_rewards
            ):
                reasons.append("reward income is non-zero but has no token-Y valuation")
            if economics.network_cost_y_atomic is None:
                reasons.append("network cost has no token-Y valuation")
            if economics.excess_vs_hold_bps is not None:
                if economics.excess_vs_hold_bps < config.min_excess_vs_hold_bps:
                    reasons.append(
                        f"excess_vs_hold_bps {economics.excess_vs_hold_bps} < required "
                        f"{config.min_excess_vs_hold_bps}"
                    )
            if (
                config.require_fee_cost_recovery
                and economics.fee_minus_entry_cost_y_atomic is not None
                and economics.fee_minus_entry_cost_y_atomic < 0
            ):
                reasons.append("trailing fee value did not recover entry/network costs")

        assessments.append(
            BaselineCandidateAssessment(
                strategy=candidate.strategy,
                half_width=candidate.half_width,
                center_offset=candidate.center_offset,
                min_bin_id=candidate.min_bin_id,
                max_bin_id=candidate.max_bin_id,
                eligible=not reasons,
                rejection_reasons=tuple(reasons),
                range_survival_ratio=candidate.range_survival_ratio,
                max_observed_share_bps=replay.max_observed_share_bps,
                economics=economics,
            )
        )

    eligible = [item for item in assessments if item.eligible]
    research_choice = max(eligible, key=_rank_key) if eligible else None
    actionable_choice = research_choice if phase2_gate.promotion_ready else None

    return BaselineSelection(
        pool_address=scan.pool_address,
        phase2_ready=phase2_gate.promotion_ready,
        phase2_blockers=tuple(phase2_gate.reasons),
        candidates_evaluated=len(assessments),
        candidates_eligible=len(eligible),
        research_choice=research_choice,
        actionable_choice=actionable_choice,
        selection_rule=(
            "lexicographic: excess_vs_hold_bps, range_survival, "
            "fee_minus_entry_cost, lower counterfactual share, narrower range"
        ),
        assessments=tuple(assessments),
    )
