from meteora_learner.cross_pool_research import (
    CrossPoolResearchCandidate,
    CrossPoolResearchReport,
)
from meteora_learner.phase_promotion import (
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
)
from meteora_learner.portfolio_allocation import (
    PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
    PortfolioAllocationCriteria,
    persist_portfolio_allocation_research,
    research_portfolio_allocation,
)
from meteora_learner.storage import Storage


def candidate(rank, pool, sized_quote=100.0, excess=100, survival=0.90):
    return CrossPoolResearchCandidate(
        rank=rank,
        pool_address=pool,
        strategy="SPOT",
        min_bin_id=-5,
        max_bin_id=5,
        half_width=5,
        center_offset=0,
        net_return_bps=150,
        hold_return_bps=50,
        excess_vs_hold_initial_bps=excess,
        range_survival_ratio=survival,
        max_observed_share_bps=100,
        sized_quote=sized_quote,
        phase2_ready=True,
        policy_authorized=True,
    )


def comparison(*items):
    return CrossPoolResearchReport(
        plans_seen=len(items),
        comparable_plans=len(items),
        excluded_plans=0,
        leader_pool_address=items[0].pool_address if items else None,
        ranking_rule="test",
        candidates=tuple(items),
    )


def promote_phase8(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def criteria(**overrides):
    values = {
        "max_positions": 3,
        "min_positions": 2,
        "max_pool_allocation_bps": 4_000,
        "min_range_survival_ratio": 0.75,
        "min_excess_vs_hold_bps": 0,
        "min_position_quote": 10.0,
        "min_budget_utilization_rate": 0.75,
    }
    values.update(overrides)
    return PortfolioAllocationCriteria(**values)


def test_equal_risk_water_fill_respects_concentration_cap(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    promote_phase8(storage)
    report = research_portfolio_allocation(
        storage,
        comparison=comparison(
            candidate(1, "pool-a"),
            candidate(2, "pool-b"),
            candidate(3, "pool-c"),
        ),
        budget_quote=90.0,
        criteria=criteria(),
    )

    assert report.status == "QUALIFIED_RESEARCH"
    assert report.research_qualified is True
    assert report.allocated_quote == 90.0
    assert report.unallocated_quote == 0.0
    assert report.selected_positions == 3
    assert [item.allocation_quote for item in report.allocations] == [
        30.0,
        30.0,
        30.0,
    ]
    assert all(
        item.allocation_bps_of_budget <= 4_000
        for item in report.allocations
    )
    assert report.policy_actionable is False


def test_concentration_caps_can_leave_budget_unallocated(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    promote_phase8(storage)
    report = research_portfolio_allocation(
        storage,
        comparison=comparison(
            candidate(1, "pool-a"),
            candidate(2, "pool-b"),
        ),
        budget_quote=100.0,
        criteria=criteria(
            max_positions=2,
            min_positions=2,
            max_pool_allocation_bps=3_000,
            min_budget_utilization_rate=0.75,
        ),
    )

    assert report.allocated_quote == 60.0
    assert report.unallocated_quote == 40.0
    assert report.max_observed_allocation_bps == 3_000
    assert report.research_qualified is False
    assert report.status == "NOT_QUALIFIED"
    assert any(
        "budget utilization" in reason
        for reason in report.reasons
    )


def test_phase8_dependency_blocks_portfolio_research_qualification(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    report = research_portfolio_allocation(
        storage,
        comparison=comparison(
            candidate(1, "pool-a"),
            candidate(2, "pool-b"),
            candidate(3, "pool-c"),
        ),
        budget_quote=90.0,
        criteria=criteria(),
    )

    assert report.status == "RESEARCH_ONLY_PHASE8_BLOCKED"
    assert report.research_qualified is False
    assert report.allocated_quote == 90.0
    assert report.policy_actionable is False


def test_ineligible_candidates_do_not_receive_allocation(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    promote_phase8(storage)
    report = research_portfolio_allocation(
        storage,
        comparison=comparison(
            candidate(1, "pool-a", excess=-10),
            candidate(2, "pool-b", survival=0.50),
            candidate(3, "pool-c"),
        ),
        budget_quote=90.0,
        criteria=criteria(min_positions=1),
    )

    assert report.eligible_candidates == 1
    assert report.selected_positions == 1
    assert [item.pool_address for item in report.allocations] == [
        "pool-c"
    ]


def test_portfolio_allocation_evidence_round_trip(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    promote_phase8(storage)
    report = research_portfolio_allocation(
        storage,
        comparison=comparison(
            candidate(1, "pool-a"),
            candidate(2, "pool-b"),
            candidate(3, "pool-c"),
        ),
        budget_quote=90.0,
        criteria=criteria(),
    )
    evidence_id = persist_portfolio_allocation_research(
        storage,
        report=report,
    )

    assert evidence_id > 0
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
    )
    assert latest is not None
    assert latest["qualified"] is True
    assert latest["status"] == "QUALIFIED_RESEARCH"
    assert latest["evidence"]["allocated_quote"] == 90.0
    assert latest["evidence"]["policy_actionable"] is False


def test_duplicate_pool_candidates_fail_closed(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    promote_phase8(storage)

    try:
        research_portfolio_allocation(
            storage,
            comparison(
                candidate(1, "pool-a"),
                candidate(2, "pool-a"),
            ),
            budget_quote=90.0,
            criteria=criteria(min_positions=1),
        )
    except ValueError as exc:
        assert "duplicate pool_address" in str(exc)
    else:
        raise AssertionError("expected duplicate-pool allocation refusal")
