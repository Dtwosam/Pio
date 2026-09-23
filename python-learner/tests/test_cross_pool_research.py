from types import SimpleNamespace

from meteora_learner.cross_pool_research import compare_phase3_research_plans


def plan(pool, *, excess, net_return, survival=1.0, share=100, safe=True):
    economics = SimpleNamespace(
        net_return_bps=net_return,
        hold_return_bps=100,
        excess_vs_hold_initial_bps=excess,
    )
    choice = SimpleNamespace(
        strategy="SPOT",
        min_bin_id=-1,
        max_bin_id=1,
        half_width=1,
        center_offset=0,
        economics=economics,
        range_survival_ratio=survival,
        max_observed_share_bps=share,
    )
    return SimpleNamespace(
        pool_address=pool,
        pool_safety=SimpleNamespace(accepted=safe),
        baseline=SimpleNamespace(research_choice=choice),
        sizing=SimpleNamespace(sized_quote=1000.0),
        entry_gate=SimpleNamespace(phase2_ready=False),
        policy_authorized=False,
    )


def test_cross_pool_ranking_uses_normalized_excess_not_raw_amounts():
    report = compare_phase3_research_plans(
        [
            plan("a", excess=50, net_return=150),
            plan("b", excess=80, net_return=120),
        ]
    )

    assert report.comparable_plans == 2
    assert report.leader_pool_address == "b"
    assert [item.pool_address for item in report.candidates] == ["b", "a"]


def test_cross_pool_ranking_breaks_ties_with_survival_then_lower_share():
    report = compare_phase3_research_plans(
        [
            plan("a", excess=80, net_return=120, survival=0.9, share=50),
            plan("b", excess=80, net_return=120, survival=1.0, share=200),
            plan("c", excess=80, net_return=120, survival=1.0, share=100),
        ]
    )

    assert [item.pool_address for item in report.candidates] == ["c", "b", "a"]


def test_cross_pool_ranking_excludes_unsafe_plan():
    report = compare_phase3_research_plans(
        [
            plan("safe", excess=10, net_return=20, safe=True),
            plan("unsafe", excess=1000, net_return=2000, safe=False),
        ]
    )

    assert report.comparable_plans == 1
    assert report.excluded_plans == 1
    assert report.leader_pool_address == "safe"
