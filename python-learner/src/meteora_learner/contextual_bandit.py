from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Sequence

from .ml_dataset import MLTrainingExample
from .phase_promotion import PHASE8, PHASE8_EVIDENCE_TYPE
from .storage import Storage


CONTEXTUAL_BANDIT_EVIDENCE_TYPE = "PHASE9_CONTEXTUAL_BANDIT_V1"


@dataclass(frozen=True)
class ContextualBanditCriteria:
    warmup_decisions_per_context: int = 2
    exploration_bonus_bps: float = 50.0
    min_decisions: int = 30
    min_pools: int = 3
    min_selected_arms: int = 2
    min_mean_uplift_vs_baseline_bps: float = 0.0
    max_mean_regret_vs_oracle_bps: float = 500.0

    def __post_init__(self) -> None:
        if self.warmup_decisions_per_context < 1:
            raise ValueError(
                "warmup_decisions_per_context must be positive"
            )
        if self.exploration_bonus_bps < 0:
            raise ValueError(
                "exploration_bonus_bps cannot be negative"
            )
        if self.min_decisions < 1:
            raise ValueError("min_decisions must be positive")
        if self.min_pools < 1:
            raise ValueError("min_pools must be positive")
        if self.min_selected_arms < 1:
            raise ValueError("min_selected_arms must be positive")
        if self.max_mean_regret_vs_oracle_bps < 0:
            raise ValueError(
                "max_mean_regret_vs_oracle_bps cannot be negative"
            )


@dataclass(frozen=True)
class BanditDecision:
    pool_address: str
    decision_observed_at: str
    context_key: str
    selected_arm: str
    selected_reward_bps: int
    baseline_arm: str
    baseline_reward_bps: int
    oracle_arm: str
    oracle_reward_bps: int
    selection_mode: str
    selected_arm_prior_count: int
    selected_arm_prior_mean_reward_bps: float | None


@dataclass(frozen=True)
class ContextualBanditReport:
    phase8_promoted: bool
    research_only: bool
    policy_actionable: bool
    status: str
    decisions_seen: int
    decisions_evaluated: int
    decisions_dropped: int
    pools_evaluated: int
    contexts_seen: int
    selected_arms: int
    warmup_decisions: int
    exploration_decisions: int
    ucb_decisions: int
    mean_selected_reward_bps: float | None
    mean_baseline_reward_bps: float | None
    mean_oracle_reward_bps: float | None
    mean_uplift_vs_baseline_bps: float | None
    mean_regret_vs_oracle_bps: float | None
    cumulative_selected_reward_bps: int
    cumulative_baseline_reward_bps: int
    cumulative_oracle_reward_bps: int
    criteria: ContextualBanditCriteria
    research_qualified: bool
    reasons: tuple[str, ...]
    decisions: tuple[BanditDecision, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _arm(example: MLTrainingExample) -> str:
    return (
        f"{example.strategy}|w={example.half_width}"
        f"|o={example.center_offset}"
    )


def _context(example: MLTrainingExample) -> str:
    if example.active_bin_move_1 > 0:
        move = "UP"
    elif example.active_bin_move_1 < 0:
        move = "DOWN"
    else:
        move = "FLAT"

    skew = (
        example.above_active_liquidity_ratio
        - example.below_active_liquidity_ratio
    )
    if skew > 0.10:
        liquidity = "ABOVE"
    elif skew < -0.10:
        liquidity = "BELOW"
    else:
        liquidity = "BALANCED"

    fee_activity = (
        "ACTIVE"
        if example.fee_growth_bins_x + example.fee_growth_bins_y > 0
        else "QUIET"
    )
    return f"{move}|{liquidity}|{fee_activity}"


def _decision_key(
    example: MLTrainingExample,
) -> tuple[str, str]:
    return (
        example.decision_observed_at,
        example.pool_address,
    )


def _validate_group(
    group: Sequence[MLTrainingExample],
) -> tuple[MLTrainingExample, str]:
    if not group:
        raise ValueError("bandit decision group cannot be empty")

    contexts = {_context(item) for item in group}
    if len(contexts) != 1:
        raise ValueError(
            "candidate actions at one decision disagree on context"
        )
    arms = [_arm(item) for item in group]
    if len(arms) != len(set(arms)):
        raise ValueError(
            "candidate actions at one decision contain duplicate arms"
        )
    baselines = [item for item in group if item.baseline_selected == 1]
    if len(baselines) != 1:
        raise ValueError(
            "bandit evaluation requires exactly one baseline action per decision"
        )
    return baselines[0], next(iter(contexts))


def evaluate_contextual_bandit(
    storage: Storage,
    *,
    examples: Sequence[MLTrainingExample],
    criteria: ContextualBanditCriteria = ContextualBanditCriteria(),
) -> ContextualBanditReport:
    if not examples:
        raise ValueError("at least one action example is required")

    phase8_promoted = storage.phase_is_promoted(
        PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
    )

    ordered = sorted(
        examples,
        key=lambda item: (
            item.decision_observed_at,
            item.pool_address,
            _arm(item),
        ),
    )
    groups: dict[tuple[str, str], list[MLTrainingExample]] = {}
    for item in ordered:
        groups.setdefault(_decision_key(item), []).append(item)

    # Statistics are pool-local to avoid pretending reward scales transfer
    # cleanly across different DLMM pools.
    stats: dict[tuple[str, str, str], tuple[int, float]] = {}
    context_visits: dict[tuple[str, str], int] = {}
    results: list[BanditDecision] = []
    dropped = 0

    for key in sorted(groups):
        group = groups[key]
        try:
            baseline, context = _validate_group(group)
        except ValueError:
            dropped += 1
            continue

        pool = baseline.pool_address
        visit_key = (pool, context)
        visits = context_visits.get(visit_key, 0)
        candidates = sorted(group, key=_arm)
        candidate_by_arm = {_arm(item): item for item in candidates}
        baseline_arm = _arm(baseline)

        if visits < criteria.warmup_decisions_per_context:
            selected = baseline
            mode = "BASELINE_WARMUP"
        else:
            unseen = [
                item
                for item in candidates
                if stats.get((pool, context, _arm(item)), (0, 0.0))[0]
                == 0
            ]
            if unseen:
                selected = unseen[0]
                mode = "UNSEEN_ARM_EXPLORATION"
            else:
                total = max(1, visits)
                scored: list[tuple[float, str, MLTrainingExample]] = []
                for item in candidates:
                    arm = _arm(item)
                    count, reward_sum = stats[
                        (pool, context, arm)
                    ]
                    mean = reward_sum / count
                    bonus = (
                        criteria.exploration_bonus_bps
                        * math.sqrt(
                            math.log(total + 1.0) / count
                        )
                    )
                    scored.append((mean + bonus, arm, item))
                # Deterministic tie-break: lexicographically smaller arm wins.
                scored.sort(key=lambda row: (-row[0], row[1]))
                selected = scored[0][2]
                mode = "CONTEXTUAL_UCB"

        selected_arm = _arm(selected)
        stat_key = (pool, context, selected_arm)
        prior_count, prior_sum = stats.get(stat_key, (0, 0.0))
        prior_mean = (
            prior_sum / prior_count if prior_count > 0 else None
        )

        selected_reward = int(selected.target_excess_vs_hold_bps)
        stats[stat_key] = (
            prior_count + 1,
            prior_sum + selected_reward,
        )
        context_visits[visit_key] = visits + 1

        oracle = max(
            candidates,
            key=lambda item: (
                item.target_excess_vs_hold_bps,
                -item.half_width,
                -abs(item.center_offset),
                _arm(item),
            ),
        )
        results.append(
            BanditDecision(
                pool_address=pool,
                decision_observed_at=selected.decision_observed_at,
                context_key=context,
                selected_arm=selected_arm,
                selected_reward_bps=selected_reward,
                baseline_arm=baseline_arm,
                baseline_reward_bps=int(
                    baseline.target_excess_vs_hold_bps
                ),
                oracle_arm=_arm(oracle),
                oracle_reward_bps=int(
                    oracle.target_excess_vs_hold_bps
                ),
                selection_mode=mode,
                selected_arm_prior_count=prior_count,
                selected_arm_prior_mean_reward_bps=prior_mean,
            )
        )

    evaluated = len(results)
    if evaluated:
        selected_rewards = [
            item.selected_reward_bps for item in results
        ]
        baseline_rewards = [
            item.baseline_reward_bps for item in results
        ]
        oracle_rewards = [
            item.oracle_reward_bps for item in results
        ]
        mean_selected = sum(selected_rewards) / evaluated
        mean_baseline = sum(baseline_rewards) / evaluated
        mean_oracle = sum(oracle_rewards) / evaluated
        uplift = mean_selected - mean_baseline
        regret = mean_oracle - mean_selected
    else:
        selected_rewards = []
        baseline_rewards = []
        oracle_rewards = []
        mean_selected = None
        mean_baseline = None
        mean_oracle = None
        uplift = None
        regret = None

    pools = {item.pool_address for item in results}
    contexts = {
        (item.pool_address, item.context_key)
        for item in results
    }
    selected_arms = {item.selected_arm for item in results}
    warmup = sum(
        item.selection_mode == "BASELINE_WARMUP"
        for item in results
    )
    exploration = sum(
        item.selection_mode == "UNSEEN_ARM_EXPLORATION"
        for item in results
    )
    ucb = sum(
        item.selection_mode == "CONTEXTUAL_UCB"
        for item in results
    )

    reasons: list[str] = []
    checks = (
        (
            phase8_promoted,
            "Phase 8 must be persistently promoted before contextual-bandit research can qualify",
        ),
        (
            evaluated >= criteria.min_decisions,
            f"evaluated decisions {evaluated} are below "
            f"{criteria.min_decisions}",
        ),
        (
            len(pools) >= criteria.min_pools,
            f"evaluated pools {len(pools)} are below "
            f"{criteria.min_pools}",
        ),
        (
            len(selected_arms) >= criteria.min_selected_arms,
            f"selected arms {len(selected_arms)} are below "
            f"{criteria.min_selected_arms}",
        ),
        (
            uplift is not None
            and uplift
            >= criteria.min_mean_uplift_vs_baseline_bps,
            "mean contextual-bandit uplift versus baseline is below required minimum",
        ),
        (
            regret is not None
            and regret
            <= criteria.max_mean_regret_vs_oracle_bps,
            "mean contextual-bandit regret versus oracle exceeds configured maximum",
        ),
    )
    reasons.extend(message for passed, message in checks if not passed)
    qualified = not reasons

    if not phase8_promoted:
        status = "RESEARCH_ONLY_PHASE8_BLOCKED"
    elif evaluated < criteria.min_decisions:
        status = "INSUFFICIENT_DECISIONS"
    elif qualified:
        status = "QUALIFIED_RESEARCH"
    else:
        status = "NOT_QUALIFIED"

    return ContextualBanditReport(
        phase8_promoted=phase8_promoted,
        research_only=True,
        policy_actionable=False,
        status=status,
        decisions_seen=len(groups),
        decisions_evaluated=evaluated,
        decisions_dropped=dropped,
        pools_evaluated=len(pools),
        contexts_seen=len(contexts),
        selected_arms=len(selected_arms),
        warmup_decisions=warmup,
        exploration_decisions=exploration,
        ucb_decisions=ucb,
        mean_selected_reward_bps=mean_selected,
        mean_baseline_reward_bps=mean_baseline,
        mean_oracle_reward_bps=mean_oracle,
        mean_uplift_vs_baseline_bps=uplift,
        mean_regret_vs_oracle_bps=regret,
        cumulative_selected_reward_bps=sum(selected_rewards),
        cumulative_baseline_reward_bps=sum(baseline_rewards),
        cumulative_oracle_reward_bps=sum(oracle_rewards),
        criteria=criteria,
        research_qualified=qualified,
        reasons=tuple(reasons),
        decisions=tuple(results),
    )


def persist_contextual_bandit_research(
    storage: Storage,
    *,
    report: ContextualBanditReport,
) -> int:
    return storage.save_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
        as_of=None,
        status=report.status,
        qualified=report.research_qualified,
        evidence=report.to_record(),
    )
