from meteora_learner.continuous_promotion import CONTINUOUS_PROMOTION_EVIDENCE_TYPE
from meteora_learner.phase8_validation import (
    Phase8PromotionCriteria,
    evaluate_phase8_promotion,
)
from meteora_learner.cross_pool_research import (
    CrossPoolResearchCandidate,
    CrossPoolResearchReport,
)
from meteora_learner.phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
    persist_phase8_promotion,
)
from meteora_learner.portfolio_allocation import (
    PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
    PORTFOLIO_CANDIDATE_EVIDENCE_TYPE,
    PortfolioAllocationCriteria,
    persist_portfolio_allocation_research,
    persist_portfolio_candidate_research,
    portfolio_candidate_artifact_sha256,
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
            comparison=comparison(
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


def test_portfolio_candidate_artifact_lineage_round_trip(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    promote_phase8(storage)
    comp = comparison(
        candidate(1, "pool-a"),
        candidate(2, "pool-b"),
    )
    evidence_id, digest = persist_portfolio_candidate_research(
        storage,
        comparison=comp,
        source_inputs=[
            {"pool_address": "pool-a"},
            {"pool_address": "pool-b"},
        ],
        assumptions={
            "account_equity_quote": 1000.0,
            "cash_quote": 1000.0,
        },
    )
    report = research_portfolio_allocation(
        storage,
        comparison=comp,
        budget_quote=100.0,
        criteria=criteria(
            max_positions=2,
            min_positions=2,
            max_pool_allocation_bps=5000,
        ),
        candidate_lineage={
            "candidate_evidence_id": evidence_id,
            "candidate_evidence_sha256": digest,
        },
    )
    allocation_id = persist_portfolio_allocation_research(
        storage,
        report=report,
    )

    assert allocation_id > evidence_id
    candidate_evidence = storage.latest_advanced_edge_evidence(
        edge_type=PORTFOLIO_CANDIDATE_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO_CANDIDATES__",
    )
    assert candidate_evidence is not None
    assert candidate_evidence["evidence"]["artifact_sha256"] == digest
    assert candidate_evidence["evidence"]["research_only"] is True
    assert candidate_evidence["evidence"]["policy_actionable"] is False

    allocation = storage.latest_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
    )
    assert allocation is not None
    assert allocation["evidence"]["candidate_lineage"] == {
        "candidate_evidence_id": evidence_id,
        "candidate_evidence_sha256": digest,
    }


def test_portfolio_candidate_hash_matches_persisted_payload(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    report = comparison(
        candidate(1, "pool-a"),
        candidate(2, "pool-b"),
    )
    source_inputs = [{"pool_address": "pool-a"}]
    assumptions = {"budget_context": "test"}

    evidence_id, digest = persist_portfolio_candidate_research(
        storage,
        comparison=report,
        source_inputs=source_inputs,
        assumptions=assumptions,
    )

    latest = storage.latest_advanced_edge_evidence(
        edge_type=PORTFOLIO_CANDIDATE_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO_CANDIDATES__",
    )
    assert latest is not None
    assert latest["id"] == evidence_id
    payload = {
        "research_only": True,
        "policy_actionable": False,
        "source_inputs": source_inputs,
        "assumptions": assumptions,
        "comparison": report.to_record(),
    }
    assert digest == portfolio_candidate_artifact_sha256(payload)
    assert latest["evidence"]["artifact_sha256"] == digest
