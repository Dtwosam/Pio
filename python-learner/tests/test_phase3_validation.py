from types import SimpleNamespace

from meteora_learner.phase3_validation import (
    Phase3PromotionCriteria,
    evaluate_phase3_promotion,
)


def report(pool, values):
    steps = tuple(
        SimpleNamespace(
            selected=True,
            forward_status="VALID",
            forward_economics=SimpleNamespace(excess_vs_hold_bps=value),
        )
        for value in values
    )
    return SimpleNamespace(
        pool_address=pool,
        steps=len(steps),
        selected_steps=len(steps),
        steps_detail=steps,
    )


def test_phase3_promotion_requires_phase2_and_walk_forward_quality():
    reports = (
        report("a", [10, 20, -5, 15]),
        report("b", [5, 8, 12, -4]),
        report("c", [7, 9, 11, 13]),
    )
    criteria = Phase3PromotionCriteria(
        min_pools=3,
        min_total_steps=12,
        min_complete_steps=12,
        min_selection_rate=1.0,
        min_complete_rate=1.0,
        min_positive_excess_rate=0.75,
        min_mean_excess_vs_hold_bps=1.0,
        max_single_step_loss_bps=10,
    )

    blocked = evaluate_phase3_promotion(
        reports,
        phase2_ready=False,
        criteria=criteria,
    )
    assert blocked.promotion_ready is False
    assert "Phase 2" in blocked.reasons[0]

    ready = evaluate_phase3_promotion(
        reports,
        phase2_ready=True,
        criteria=criteria,
    )
    assert ready.promotion_ready is True
    assert ready.pools_seen == 3
    assert ready.complete_steps == 12
    assert ready.positive_excess_rate == 10 / 12
    assert ready.worst_excess_vs_hold_bps == -5


def test_phase3_promotion_blocks_excessive_single_step_loss():
    reports = (
        report("a", [20, 20]),
        report("b", [20, -200]),
    )
    result = evaluate_phase3_promotion(
        reports,
        phase2_ready=True,
        criteria=Phase3PromotionCriteria(
            min_pools=2,
            min_total_steps=4,
            min_complete_steps=4,
            min_selection_rate=1.0,
            min_complete_rate=1.0,
            min_positive_excess_rate=0.5,
            min_mean_excess_vs_hold_bps=-100,
            max_single_step_loss_bps=100,
        ),
    )

    assert result.promotion_ready is False
    assert any("worst_excess" in reason for reason in result.reasons)
