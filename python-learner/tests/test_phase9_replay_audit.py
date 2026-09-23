from meteora_learner.phase9_replay_audit import (
    evaluate_phase9_replay_audit,
)
from meteora_learner.phase9_validation import Phase9ResearchBundleCriteria
from meteora_learner.storage import Storage
from meteora_learner.wallet_flow import WALLET_FLOW_EVIDENCE_TYPE


def test_replay_audit_reports_missing_required_families(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = evaluate_phase9_replay_audit(storage)

    assert report.verified is False
    assert report.research_only is True
    assert report.policy_actionable is False
    statuses = {item.family: item.status for item in report.families}
    assert statuses["adaptive_regime"] == "MISSING_EVIDENCE"
    assert statuses["mint_risk"] == "MISSING_EVIDENCE"
    assert statuses["wallet_flow"] == "MISSING_EVIDENCE"
    assert statuses["portfolio_allocation"] == "MISSING_EVIDENCE"
    assert statuses["static_hedge"] == "MISSING_EVIDENCE"
    assert statuses["contextual_bandit"] == "MISSING_EVIDENCE"
    assert len(report.reasons) == 6


def test_replay_audit_exposes_boundary_violation_before_replay(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    evidence_id = storage.save_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:00:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": False,
            "policy_actionable": True,
            "research_qualified": True,
        },
    )

    report = evaluate_phase9_replay_audit(
        storage,
        criteria=Phase9ResearchBundleCriteria(
            min_mint_risk_pools=1,
            min_wallet_flow_pools=1,
            min_static_hedge_pools=1,
        ),
    )

    wallet = next(
        item for item in report.families
        if item.family == "wallet_flow"
    )
    assert wallet.latest_evidence_ids == (evidence_id,)
    assert wallet.qualified_evidence_ids == ()
    assert wallet.boundary_valid is False
    assert wallet.replay_verified is False
    assert wallet.status == "BOUNDARY_INVALID"
    assert "research-only boundary" in wallet.reason


def test_optional_family_does_not_block_overall_audit(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    criteria = Phase9ResearchBundleCriteria(
        min_mint_risk_pools=1,
        min_wallet_flow_pools=1,
        min_static_hedge_pools=1,
        require_portfolio_allocation=False,
        require_contextual_bandit=False,
        require_adaptive_multi_pool=False,
    )

    report = evaluate_phase9_replay_audit(
        storage,
        criteria=criteria,
    )

    required = {
        item.family
        for item in report.families
        if item.required_records > 0
    }
    assert required == {
        "mint_risk",
        "wallet_flow",
        "static_hedge",
    }
    assert report.verified is False
