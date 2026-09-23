from dataclasses import replace

from meteora_learner.contextual_bandit import (
    CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
    ContextualBanditCriteria,
    evaluate_contextual_bandit,
    persist_contextual_bandit_research,
)
from meteora_learner.ml_dataset import MLTrainingExample
from meteora_learner.phase_promotion import PHASE8, PHASE8_EVIDENCE_TYPE
from meteora_learner.storage import Storage


def example(
    pool,
    decision,
    *,
    strategy,
    width,
    reward,
    baseline=False,
):
    return MLTrainingExample(
        pool_address=pool,
        decision_observed_at=f"2026-09-23T{decision:02d}:00:00+00:00",
        forward_end_observed_at=f"2026-09-23T{decision:02d}:30:00+00:00",
        strategy=strategy,
        baseline_selected=int(baseline),
        strategy_spot=int(strategy == "SPOT"),
        strategy_curve=int(strategy == "CURVE"),
        strategy_bid_ask=0,
        half_width=width,
        center_offset=0,
        range_width_bins=width * 2 + 1,
        active_bin_id=0,
        active_bin_move_1=1,
        deposit_fee_rate_bps=1.0,
        occupied_bins=5,
        active_liquidity_ratio=0.2,
        near_active_liquidity_ratio=0.8,
        below_active_liquidity_ratio=0.4,
        above_active_liquidity_ratio=0.4,
        liquidity_weighted_distance_bins=1.0,
        fee_growth_bins_x=1,
        fee_growth_bins_y=0,
        trailing_range_survival_ratio=0.8,
        trailing_excess_vs_hold_bps=10,
        trailing_net_return_bps=20,
        trailing_max_observed_share_bps=100,
        target_net_return_bps=reward,
        target_excess_vs_hold_bps=reward,
        target_range_survival_ratio=0.8,
        target_positive_excess=int(reward > 0),
    )


def corpus(pools=("pool-a",), decisions=6):
    output = []
    for pool in pools:
        for decision in range(decisions):
            output.extend(
                [
                    example(
                        pool,
                        decision,
                        strategy="SPOT",
                        width=2,
                        reward=20 + decision,
                        baseline=True,
                    ),
                    example(
                        pool,
                        decision,
                        strategy="CURVE",
                        width=4,
                        reward=40 - decision,
                    ),
                ]
            )
    return output


def promote_phase8(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def criteria(**overrides):
    values = {
        "warmup_decisions_per_context": 1,
        "exploration_bonus_bps": 10.0,
        "min_decisions": 4,
        "min_pools": 1,
        "min_selected_arms": 2,
        "min_mean_uplift_vs_baseline_bps": -100.0,
        "max_mean_regret_vs_oracle_bps": 500.0,
    }
    values.update(overrides)
    return ContextualBanditCriteria(**values)


def test_current_decision_rewards_do_not_change_current_selection(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    promote_phase8(storage)
    original = corpus(decisions=5)

    base = evaluate_contextual_bandit(
        storage,
        examples=original,
        criteria=criteria(),
    )

    target_time = "2026-09-23T03:00:00+00:00"
    mutated = [
        replace(
            item,
            target_excess_vs_hold_bps=(
                100_000
                if item.strategy == "CURVE"
                else -100_000
            ),
        )
        if item.decision_observed_at == target_time
        else item
        for item in original
    ]
    changed = evaluate_contextual_bandit(
        storage,
        examples=mutated,
        criteria=criteria(),
    )

    base_decision = next(
        item for item in base.decisions
        if item.decision_observed_at == target_time
    )
    changed_decision = next(
        item for item in changed.decisions
        if item.decision_observed_at == target_time
    )
    assert changed_decision.selected_arm == base_decision.selected_arm
    assert changed_decision.selection_mode == base_decision.selection_mode


def test_future_rewards_do_not_change_earlier_bandit_decisions(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    promote_phase8(storage)
    original = corpus(decisions=6)
    base = evaluate_contextual_bandit(
        storage,
        examples=original,
        criteria=criteria(),
    )

    mutated = [
        replace(item, target_excess_vs_hold_bps=-99999)
        if item.decision_observed_at
        == "2026-09-23T05:00:00+00:00"
        else item
        for item in original
    ]
    changed = evaluate_contextual_bandit(
        storage,
        examples=mutated,
        criteria=criteria(),
    )

    assert [
        (item.decision_observed_at, item.selected_arm)
        for item in changed.decisions[:-1]
    ] == [
        (item.decision_observed_at, item.selected_arm)
        for item in base.decisions[:-1]
    ]


def test_bandit_requires_phase8_and_persists_research_only_evidence(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    examples = corpus(
        pools=("pool-a", "pool-b", "pool-c"),
        decisions=4,
    )

    blocked = evaluate_contextual_bandit(
        storage,
        examples=examples,
        criteria=criteria(min_pools=3, min_decisions=12),
    )
    assert blocked.research_qualified is False
    assert blocked.policy_actionable is False
    assert blocked.status == "RESEARCH_ONLY_PHASE8_BLOCKED"

    promote_phase8(storage)
    report = evaluate_contextual_bandit(
        storage,
        examples=examples,
        criteria=criteria(min_pools=3, min_decisions=12),
    )
    assert report.research_qualified is True
    assert report.policy_actionable is False

    evidence_id = persist_contextual_bandit_research(
        storage,
        report=report,
    )
    assert evidence_id > 0
    latest = storage.latest_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
    )
    assert latest is not None
    assert latest["evidence"]["research_only"] is True
    assert latest["evidence"]["policy_actionable"] is False
