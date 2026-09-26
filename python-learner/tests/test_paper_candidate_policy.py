from meteora_learner.ml_dataset import MLTrainingExample
from meteora_learner.paper_candidate_policy import (
    PaperCandidateArm,
    candidate_context_key,
    select_empirical_paper_candidate,
)


def example(
    *,
    pool="pool",
    strategy="SPOT",
    width=1,
    offset=0,
    excess=10,
    net=12,
    move=1,
    below=0.2,
    above=0.5,
    fee_x=1,
    fee_y=0,
):
    return MLTrainingExample(
        pool_address=pool,
        decision_observed_at="2026-09-20T00:00:00+00:00",
        forward_end_observed_at="2026-09-20T00:10:00+00:00",
        strategy=strategy,
        baseline_selected=1 if strategy == "SPOT" else 0,
        strategy_spot=1 if strategy == "SPOT" else 0,
        strategy_curve=1 if strategy == "CURVE" else 0,
        strategy_bid_ask=1 if strategy == "BID_ASK" else 0,
        half_width=width,
        center_offset=offset,
        range_width_bins=width * 2 + 1,
        active_bin_id=100,
        active_bin_move_1=move,
        occupied_bins=3,
        deposit_fee_rate_bps=1.0,
        active_liquidity_ratio=0.2,
        near_active_liquidity_ratio=0.6,
        below_active_liquidity_ratio=below,
        above_active_liquidity_ratio=above,
        liquidity_weighted_distance_bins=1.0,
        fee_growth_bins_x=fee_x,
        fee_growth_bins_y=fee_y,
        trailing_excess_vs_hold_bps=0,
        trailing_net_return_bps=0,
        trailing_range_survival_ratio=1.0,
        trailing_max_observed_share_bps=100,
        target_net_return_bps=net,
        target_excess_vs_hold_bps=excess,
        target_positive_excess=1 if excess > 0 else 0,
        target_range_survival_ratio=0.8,
    )


CONTEXT = candidate_context_key(
    active_bin_move_1=1,
    below_active_liquidity_ratio=0.2,
    above_active_liquidity_ratio=0.5,
    fee_growth_bins_x=1,
    fee_growth_bins_y=0,
)


def test_empirical_policy_uses_same_pool_and_context_only():
    candidates = (
        PaperCandidateArm("SPOT", 1, 0),
        PaperCandidateArm("CURVE", 1, 0),
    )
    result = select_empirical_paper_candidate(
        historical_examples=(
            example(strategy="SPOT", excess=5),
            example(strategy="SPOT", excess=15),
            example(strategy="CURVE", excess=30),
            example(pool="other", strategy="CURVE", excess=1000),
            example(strategy="CURVE", excess=1000, move=-1),
        ),
        pool_address="pool",
        context_key=CONTEXT,
        current_candidates=candidates,
    )

    assert result.status == "PAPER_EMPIRICAL_SELECTION"
    assert result.selected_arm == PaperCandidateArm("CURVE", 1, 0)
    assert result.paper_only is True
    assert result.live_authorized is False
    by_arm = {item.arm.key: item for item in result.evidence}
    assert by_arm["SPOT|w=1|o=0"].mean_excess_vs_hold_bps == 10.0
    assert by_arm["CURVE|w=1|o=0"].mean_excess_vs_hold_bps == 30.0


def test_empirical_policy_explores_unseen_arm_in_paper_only():
    result = select_empirical_paper_candidate(
        historical_examples=(example(strategy="SPOT"),),
        pool_address="pool",
        context_key=CONTEXT,
        current_candidates=(
            PaperCandidateArm("SPOT", 1, 0),
            PaperCandidateArm("CURVE", 1, 0),
        ),
    )

    assert result.status == "PAPER_EXPLORATION"
    assert result.selection_mode == "UNSEEN_ARM"
    assert result.selected_arm == PaperCandidateArm("CURVE", 1, 0)
    assert result.live_authorized is False


def test_empirical_policy_makes_no_selection_without_matching_context():
    result = select_empirical_paper_candidate(
        historical_examples=(example(move=-1),),
        pool_address="pool",
        context_key=CONTEXT,
        current_candidates=(PaperCandidateArm("SPOT", 1, 0),),
    )

    assert result.status == "INSUFFICIENT_CONTEXT_EVIDENCE"
    assert result.selected_arm is None
    assert result.live_authorized is False
