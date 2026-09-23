from dataclasses import replace

from meteora_learner.contextual_bandit import (
    CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
)
from meteora_learner.mint_risk import MINT_RISK_EVIDENCE_TYPE
from meteora_learner.phase9_research import (
    PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
)
from meteora_learner.phase9_validation import (
    PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE,
    Phase9ResearchBundleCriteria,
    evaluate_phase9_promotion,
    evaluate_phase9_research_bundle,
    persist_phase9_research_bundle,
)
from meteora_learner.phase_promotion import (
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
    PHASE9,
    persist_phase9_promotion,
    phase_promotion_state,
)
from meteora_learner.portfolio_allocation import (
    PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
)
from meteora_learner.static_hedge import STATIC_HEDGE_EVIDENCE_TYPE
from meteora_learner.storage import Storage
from meteora_learner.wallet_flow import WALLET_FLOW_EVIDENCE_TYPE


def promote_phase8(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def seed_portfolio_candidate_lineage(storage):
    artifact_sha = "portfolio-deadbeef"
    evidence_id = storage.save_advanced_edge_evidence(
        edge_type="PHASE9_PORTFOLIO_CANDIDATES_V1",
        pool_address="__PORTFOLIO_CANDIDATES__",
        as_of="2026-09-23T12:00:00+00:00",
        status="BUILT",
        qualified=True,
        evidence={
            "artifact_sha256": artifact_sha,
            "research_only": True,
            "policy_actionable": False,
            "source_inputs": [{"pool_address": "pool-a"}],
            "assumptions": {"budget_context": "test"},
            "comparison": {"candidates": [{"pool_address": "pool-a"}]},
        },
    )
    return {
        "candidate_evidence_id": evidence_id,
        "candidate_evidence_sha256": artifact_sha,
    }


def seed_bandit_dataset_lineage(storage):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status,
                metrics_json
            ) VALUES (
                'champion', '2026-09-23T00:00:00+00:00',
                '2026-09-23T00:00:00+00:00',
                'TEST', 'TEST', 'dataset-v1', 'CHAMPION', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO continuous_learning_cycles(
                cycle_id, created_at, updated_at, status, active_key,
                champion_model_id, champion_dataset_version,
                champion_evidence_watermark, plan_evidence_id,
                plan_as_of, target_dataset_version,
                challenger_model_id, plan_json, notes
            ) VALUES (
                'cycle', '2026-09-23T00:00:00+00:00',
                '2026-09-23T00:00:00+00:00',
                'PLANNED', NULL, 'champion', 'dataset-v1',
                '2026-09-23T00:00:00+00:00', 1,
                '2026-09-23T12:00:00+00:00',
                'ML_ACTION_DATASET_V1:test',
                NULL, '{}', NULL
            )
            """
        )
    evidence_id = storage.save_model_live_evidence(
        model_id="champion",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
        status="BUILT",
        evidence={
            "cycle_id": "cycle",
            "cutoff": "2026-09-23T12:00:00+00:00",
            "target_dataset_version": "ML_ACTION_DATASET_V1:test",
            "dataset": {
                "dataset_sha256": "deadbeef",
                "dataset_version": "ML_ACTION_DATASET_V1:test",
            },
            "output_file": "retrain.csv",
        },
    )
    return {
        "cycle_id": "cycle",
        "champion_model_id": "champion",
        "dataset_evidence_id": evidence_id,
        "dataset_version": "ML_ACTION_DATASET_V1:test",
        "dataset_sha256": "deadbeef",
        "cutoff": "2026-09-23T12:00:00+00:00",
        "output_file": "retrain.csv",
    }


def evidence(
    storage,
    edge_type,
    pool,
    *,
    qualified=True,
    research_only=True,
    policy_actionable=False,
    extra=None,
):
    return storage.save_advanced_edge_evidence(
        edge_type=edge_type,
        pool_address=pool,
        as_of="2026-09-23T12:00:00+00:00",
        status=(
            "QUALIFIED_RESEARCH"
            if qualified
            else "NOT_QUALIFIED"
        ),
        qualified=qualified,
        evidence={
            "research_qualified": qualified,
            "research_only": research_only,
            "policy_actionable": policy_actionable,
            **(extra or {}),
        },
    )


def seed_ready(storage):
    promote_phase8(storage)
    portfolio_lineage = seed_portfolio_candidate_lineage(storage)
    bandit_lineage = seed_bandit_dataset_lineage(storage)
    evidence(
        storage,
        PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        "__MULTI_POOL__",
    )
    for pool in ("pool-a", "pool-b"):
        evidence(storage, MINT_RISK_EVIDENCE_TYPE, pool)
        evidence(storage, WALLET_FLOW_EVIDENCE_TYPE, pool)
    evidence(
        storage,
        PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        "__PORTFOLIO__",
        extra={"candidate_lineage": portfolio_lineage},
    )
    evidence(storage, STATIC_HEDGE_EVIDENCE_TYPE, "pool-a")
    evidence(
        storage,
        CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        "__CONTEXTUAL_BANDIT__",
        extra={"dataset_lineage": bandit_lineage},
    )


def test_phase9_bundle_ready_with_complete_research_corpus(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is True
    assert report.status == "RESEARCH_BUNDLE_READY"
    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.mint_risk.qualified_records == 2
    assert report.wallet_flow.qualified_records == 2

    evidence_id = persist_phase9_research_bundle(
        storage,
        report=report,
    )
    assert evidence_id > 0
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE,
        pool_address="__PHASE9_RESEARCH__",
    )
    assert latest is not None
    assert latest["qualified"] is True
    assert latest["evidence"]["policy_actionable"] is False


def test_missing_research_family_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            DELETE FROM advanced_edge_evidence
            WHERE edge_type = ?
            """,
            (CONTEXTUAL_BANDIT_EVIDENCE_TYPE,),
        )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert report.status == "RESEARCH_BUNDLE_INCOMPLETE"
    assert any(
        "contextual-bandit" in reason
        for reason in report.reasons
    )


def test_latest_boundary_violation_invalidates_old_qualified_evidence(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    evidence(
        storage,
        MINT_RISK_EVIDENCE_TYPE,
        "pool-a",
        qualified=True,
        research_only=False,
        policy_actionable=True,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert report.mint_risk.boundary_valid is False
    assert any(
        "mint risk evidence violates" in reason
        for reason in report.reasons
    )


def test_phase8_promotion_is_required(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    evidence(
        storage,
        PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        "__MULTI_POOL__",
    )
    for pool in ("pool-a", "pool-b"):
        evidence(storage, MINT_RISK_EVIDENCE_TYPE, pool)
        evidence(storage, WALLET_FLOW_EVIDENCE_TYPE, pool)
    evidence(
        storage,
        PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        "__PORTFOLIO__",
    )
    evidence(storage, STATIC_HEDGE_EVIDENCE_TYPE, "pool-a")
    evidence(
        storage,
        CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        "__CONTEXTUAL_BANDIT__",
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert report.status == "RESEARCH_ONLY_PHASE8_BLOCKED"


def test_bundle_criteria_can_require_more_pool_diversity(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)

    report = evaluate_phase9_research_bundle(
        storage,
        criteria=Phase9ResearchBundleCriteria(
            min_mint_risk_pools=3,
            min_wallet_flow_pools=3,
            min_static_hedge_pools=2,
        ),
    )

    assert report.research_ready is False
    assert any(
        "mint-risk pools 2 are below 3" in reason
        for reason in report.reasons
    )
    assert any(
        "wallet-flow pools 2 are below 3" in reason
        for reason in report.reasons
    )
    assert any(
        "static-hedge pools 1 are below 2" in reason
        for reason in report.reasons
    )


def test_phase9_promotion_persists_non_actionable_ready_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)

    bundle = evaluate_phase9_research_bundle(storage)
    bundle_id = persist_phase9_research_bundle(
        storage,
        report=bundle,
    )
    report = evaluate_phase9_promotion(storage)

    assert report.promotion_ready is True
    assert report.research_bundle_evidence_id == bundle_id
    assert report.persisted_bundle_matches_current is True
    assert report.research_only is True
    assert report.policy_actionable is False

    state = persist_phase9_promotion(
        storage,
        report=report,
    )
    assert state.phase_name == PHASE9
    assert state.promoted is True
    assert phase_promotion_state(
        storage,
        phase_name=PHASE9,
    ).promoted is True


def test_phase9_promotion_requires_complete_research_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            DELETE FROM advanced_edge_evidence
            WHERE edge_type = ?
            """,
            (STATIC_HEDGE_EVIDENCE_TYPE,),
        )

    report = evaluate_phase9_promotion(storage)

    assert report.promotion_ready is False
    assert report.policy_actionable is False
    assert any(
        "static-hedge" in reason
        for reason in report.reasons
    )


def test_phase9_promotion_rejects_stale_persisted_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(
        storage,
        report=bundle,
    )

    evidence(
        storage,
        MINT_RISK_EVIDENCE_TYPE,
        "pool-c",
    )

    report = evaluate_phase9_promotion(storage)

    assert report.promotion_ready is False
    assert report.persisted_bundle_matches_current is False
    assert any(
        "stale versus current evidence" in reason
        for reason in report.reasons
    )


def test_phase9_promotion_refuses_live_policy_authority(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(
        storage,
        report=bundle,
    )
    report = evaluate_phase9_promotion(storage)
    assert report.promotion_ready is True

    actionable = replace(
        report,
        policy_actionable=True,
    )
    try:
        persist_phase9_promotion(
            storage,
            report=actionable,
        )
    except ValueError as exc:
        assert "must not grant live-policy authority" in str(exc)
    else:
        raise AssertionError(
            "expected actionable Phase 9 promotion refusal"
        )

    not_research_only = replace(
        report,
        research_only=False,
    )
    try:
        persist_phase9_promotion(
            storage,
            report=not_research_only,
        )
    except ValueError as exc:
        assert "must remain research-only" in str(exc)
    else:
        raise AssertionError(
            "expected non-research Phase 9 promotion refusal"
        )


def test_forged_bandit_dataset_lineage_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["dataset_lineage"] = {
        **forged["dataset_lineage"],
        "dataset_evidence_id": 999999,
    }
    storage.save_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
        as_of="2026-09-23T12:01:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "checksum-verified retraining dataset lineage" in reason
        for reason in report.reasons
    )
