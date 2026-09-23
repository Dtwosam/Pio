from dataclasses import replace

from meteora_learner.continuous_promotion import CONTINUOUS_PROMOTION_EVIDENCE_TYPE
from meteora_learner.phase8_validation import (
    Phase8PromotionCriteria,
    evaluate_phase8_promotion,
)
from meteora_learner.contextual_bandit import (
    CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
    ContextualBanditCriteria,
    evaluate_contextual_bandit,
    persist_contextual_bandit_research,
)
from meteora_learner.ml_dataset import MLTrainingExample
from meteora_learner.phase_promotion import PHASE8, PHASE8_EVIDENCE_TYPE
from meteora_learner.phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
    persist_phase8_promotion,
)
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
        phase_name=PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status, metrics_json
            ) VALUES (
                'champion', '2026-09-20T00:00:00+00:00',
                '2026-09-22T00:00:00+00:00',
                'TEST', 'TEST', 'dataset-v1', 'CHAMPION', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO continuous_learning_cycles(
                cycle_id, created_at, updated_at, status,
                active_key, champion_model_id,
                champion_dataset_version,
                champion_evidence_watermark,
                plan_evidence_id, plan_as_of,
                target_dataset_version,
                challenger_model_id, plan_json
            ) VALUES (
                'phase8-cycle',
                '2026-09-21T00:00:00+00:00',
                '2026-09-22T00:00:00+00:00',
                'COMPLETED', NULL,
                'old-champion', 'dataset-v0',
                '2026-09-20T00:00:00+00:00',
                1, '2026-09-21T00:00:00+00:00',
                'dataset-v1', 'champion', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_learning_labels(
                position_address, decision_id, pool_address,
                model_version, strategy,
                min_bin_id, max_bin_id, range_width_bins,
                proposed_capital_quote,
                expected_net_return_pct, expected_downside_pct,
                realized_pnl_quote, realized_return_bps,
                prediction_error_bps, target_positive_return,
                quote_unit, opened_signature, closed_decision_id,
                created_at, raw_json
            ) VALUES (
                'phase8-position', 'phase8-decision',
                'phase8-pool', 'champion', 'SPOT',
                -1, 1, 3, '10', '1', '1',
                '1', 100, 0, 1, 'USD',
                'phase8-signature', 'phase8-close',
                '2026-09-23T00:00:00+00:00', '{}'
            )
            """
        )
    storage.save_model_live_evidence(
        model_id="champion",
        evidence_type=CONTINUOUS_PROMOTION_EVIDENCE_TYPE,
        status="CHAMPION",
        evidence={
            "cycle_id": "phase8-cycle",
            "predecessor_model_id": "old-champion",
            "validation": {"qualified": True},
        },
    )
    report = evaluate_phase8_promotion(
        storage,
        criteria=Phase8PromotionCriteria(
            min_completed_cycles=1,
            min_live_labels=1,
            min_live_pools=1,
            max_realized_drawdown_bps=10_000,
            max_single_loss_bps=10_000,
            min_win_rate=0.0,
            min_mean_return_bps=-10_000,
            max_mean_abs_prediction_error_bps=10_000,
        ),
    )
    assert report.promotion_ready is True
    persist_phase8_promotion(storage, report=report)
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
