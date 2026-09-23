import sqlite3
import hashlib
from pathlib import Path
from dataclasses import asdict, replace

from meteora_learner.adaptive_range import AdaptiveRangeCriteria
from meteora_learner.adaptive_range_validation import (
    AdaptiveRangeValidationCriteria,
)
from meteora_learner.chain_snapshot_lineage import (
    chain_snapshot_source_record,
    chain_snapshot_source_sha256,
)
from meteora_learner.contextual_bandit import (
    CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
    ContextualBanditCriteria,
)
from meteora_learner.cross_pool_research import (
    CrossPoolResearchCandidate,
    CrossPoolResearchReport,
)
from meteora_learner.contextual_bandit_cycle import (
    evaluate_cycle_contextual_bandit,
    persist_cycle_contextual_bandit,
)
from meteora_learner.market_regime import DLMMRegimeCriteria
from meteora_learner.mint_risk import (
    MINT_RISK_EVIDENCE_TYPE,
    persist_pool_mint_risk,
    research_pool_mint_risk,
)
from meteora_learner.phase9_research import (
    PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
    Phase9ResearchCriteria,
    evaluate_phase9_research,
    persist_phase9_research,
)
from meteora_learner.phase9_validation import (
    PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE,
    Phase9ResearchBundleCriteria,
    audit_persisted_phase9_promotion,
    evaluate_phase9_promotion,
    evaluate_phase9_research_bundle,
    persist_phase9_research_bundle,
    phase9_research_bundle_sha256,
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
    PortfolioAllocationCriteria,
    persist_portfolio_allocation_research,
    persist_portfolio_candidate_research,
    portfolio_candidate_artifact_sha256,
    research_portfolio_allocation,
)
from meteora_learner.static_hedge import (
    STATIC_HEDGE_EVIDENCE_TYPE,
    HedgeInstrumentAssumptions,
    StaticHedgeCriteria,
    persist_static_hedge_research,
    research_static_inventory_hedge,
)
from meteora_learner.storage import Storage
from meteora_learner.wallet_flow import (
    WALLET_FLOW_EVIDENCE_TYPE,
    WalletFlowCriteria,
    persist_wallet_flow_research,
    research_wallet_flow,
    wallet_flow_source_sha256,
)


def promote_phase8(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def seed_portfolio_candidate_lineage(storage):
    comparison = CrossPoolResearchReport(
        plans_seen=2,
        comparable_plans=2,
        excluded_plans=0,
        leader_pool_address="pool-a",
        ranking_rule="test-ranking",
        candidates=(
            CrossPoolResearchCandidate(
                rank=1,
                pool_address="pool-a",
                strategy="SPOT",
                min_bin_id=-1,
                max_bin_id=1,
                half_width=1,
                center_offset=0,
                net_return_bps=100,
                hold_return_bps=50,
                excess_vs_hold_initial_bps=50,
                range_survival_ratio=1.0,
                max_observed_share_bps=100,
                sized_quote=60.0,
                phase2_ready=True,
                policy_authorized=True,
            ),
            CrossPoolResearchCandidate(
                rank=2,
                pool_address="pool-b",
                strategy="CURVE",
                min_bin_id=-2,
                max_bin_id=2,
                half_width=2,
                center_offset=0,
                net_return_bps=90,
                hold_return_bps=50,
                excess_vs_hold_initial_bps=40,
                range_survival_ratio=1.0,
                max_observed_share_bps=100,
                sized_quote=60.0,
                phase2_ready=True,
                policy_authorized=True,
            ),
        ),
    )
    evidence_id, artifact_sha = persist_portfolio_candidate_research(
        storage,
        comparison=comparison,
        source_inputs=[
            {"pool_address": "pool-a"},
            {"pool_address": "pool-b"},
        ],
        assumptions={"budget_context": "test"},
    )
    lineage = {
        "candidate_evidence_id": evidence_id,
        "candidate_evidence_sha256": artifact_sha,
    }
    report = research_portfolio_allocation(
        storage,
        comparison=comparison,
        budget_quote=100.0,
        criteria=PortfolioAllocationCriteria(
            max_positions=2,
            min_positions=2,
            max_pool_allocation_bps=6_000,
            min_range_survival_ratio=0.5,
            min_excess_vs_hold_bps=0,
            min_position_quote=10.0,
            min_budget_utilization_rate=0.9,
        ),
        candidate_lineage=lineage,
    )
    assert report.research_qualified is True
    persist_portfolio_allocation_research(
        storage,
        report=report,
    )
    return lineage


def seed_bandit_dataset_lineage(storage):
    dataset_path = Path(storage.path).parent / "phase9-bandit.csv"
    dataset_path.write_text(
        "pool_address,decision_observed_at,forward_end_observed_at,"
        "strategy,baseline_selected,strategy_spot,strategy_curve,"
        "strategy_bid_ask,half_width,center_offset,range_width_bins,"
        "active_bin_id,active_bin_move_1,occupied_bins,"
        "fee_growth_bins_x,fee_growth_bins_y,"
        "trailing_excess_vs_hold_bps,trailing_net_return_bps,"
        "trailing_max_observed_share_bps,target_net_return_bps,"
        "target_excess_vs_hold_bps,target_positive_excess,"
        "deposit_fee_rate_bps,active_liquidity_ratio,"
        "near_active_liquidity_ratio,below_active_liquidity_ratio,"
        "above_active_liquidity_ratio,liquidity_weighted_distance_bins,"
        "trailing_range_survival_ratio,target_range_survival_ratio\n"
        "pool-a,2026-09-23T10:00:00+00:00,"
        "2026-09-23T11:00:00+00:00,SPOT,1,1,0,0,1,0,3,"
        "0,0,3,0,0,0,0,100,10,10,1,0,0.5,0.5,0.25,0.25,"
        "1.0,1.0,1.0\n"
        "pool-a,2026-09-23T10:00:00+00:00,"
        "2026-09-23T11:00:00+00:00,CURVE,0,0,1,0,2,0,5,"
        "0,0,3,0,0,0,0,100,20,20,1,0,0.5,0.5,0.25,0.25,"
        "1.0,1.0,1.0\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    dataset_version = f"ML_ACTION_DATASET_V1:{digest[:16]}"
    cutoff = "2026-09-23T12:00:00+00:00"

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
                ?, ?, NULL, '{}', NULL
            )
            """,
            (cutoff, dataset_version),
        )
    storage.save_model_live_evidence(
        model_id="champion",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
        status="BUILT",
        evidence={
            "cycle_id": "cycle",
            "cutoff": cutoff,
            "target_dataset_version": dataset_version,
            "dataset": {
                "dataset_sha256": digest,
                "dataset_version": dataset_version,
            },
            "output_file": str(dataset_path),
        },
    )

    result = evaluate_cycle_contextual_bandit(
        storage,
        cycle_id="cycle",
        criteria=ContextualBanditCriteria(
            warmup_decisions_per_context=1,
            exploration_bonus_bps=0.0,
            min_decisions=1,
            min_pools=1,
            min_selected_arms=1,
            min_mean_uplift_vs_baseline_bps=-10_000.0,
            max_mean_regret_vs_oracle_bps=10_000.0,
        ),
    )
    assert result.report.research_qualified is True
    persist_cycle_contextual_bandit(storage, result=result)
    return asdict(result.lineage)


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


def seed_wallet_flow_lineage(storage, pool):
    created_at = "2026-09-23T12:00:00+00:00"
    signature = f"sig-{pool}"
    position = f"position-{pool}"
    user = f"user-{pool}"
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO position_event_history(
                observed_at, position_address, signature, ix_index,
                event_type, block_time, slot, pool_address,
                user_address, token_x, token_y,
                amount_x, amount_y, amount_x_usd, amount_y_usd,
                total_usd, created_at, raw_json
            ) VALUES (
                ?, ?, ?, 0, 'ADD_LIQUIDITY', 1, 1, ?,
                ?, 'X', 'Y', '1', '1', '50', '50',
                '100', ?, '{}'
            )
            """,
            (
                created_at,
                position,
                signature,
                pool,
                user,
                created_at,
            ),
        )

    report = research_wallet_flow(
        storage,
        pool_address=pool,
        criteria=WalletFlowCriteria(
            lookback_events=10,
            min_events=1,
            min_unique_users=1,
            max_top_user_share_bps=10_000,
        ),
        as_of=created_at,
    )
    assert report.research_qualified is True
    persist_wallet_flow_research(storage, report=report)


def seed_mint_risk_lineage(storage, pool):
    observed_at = "2026-09-23T12:00:00+00:00"
    token_program = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    x = f"{pool}-x"
    y = f"{pool}-y"
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(
                observed_at, pool_address, active_bin_id, bin_step,
                token_x_mint, token_y_mint,
                token_x_program, token_y_program, raw_json
            ) VALUES (?, ?, 0, 25, ?, ?, ?, ?, '{}')
            """,
            (observed_at, pool, x, y, token_program, token_program),
        )

    for mint in (x, y):
        storage.save_token_mint_snapshot(
            {
                "mint_address": mint,
                "token_program": token_program,
                "capture_slot_start": 1,
                "capture_slot_end": 2,
                "supply": "1000000",
                "decimals": 6,
                "is_initialized": True,
                "mint_authority": None,
                "freeze_authority": None,
                "data_len": 82,
                "token_2022_extension_data_len": 0,
                "has_token_2022_extension_data": False,
            },
            observed_at=observed_at,
        )

    report = research_pool_mint_risk(
        storage,
        pool_address=pool,
        as_of=observed_at,
    )
    assert report.research_qualified is True
    assert report.pool_snapshot_sha256
    assert all(
        item.mint_snapshot_sha256
        for item in report.assessments
    )
    persist_pool_mint_risk(storage, report=report)


def seed_adaptive_multi_pool_lineage(storage, pools):
    values = [
        100, 102, 104, 102, 100, 102, 104, 102,
        100, 102, 104, 102, 100, 102, 104, 102,
        100, 102, 104, 102, 100, 102, 104, 102,
    ]
    for pool_index, pool in enumerate(pools):
        with storage.connect() as conn:
            for index, value in enumerate(values):
                conn.execute(
                    """
                    INSERT INTO chain_pool_snapshots(
                        observed_at, pool_address, active_bin_id,
                        bin_step, token_x_mint, token_y_mint,
                        raw_json
                    ) VALUES (?, ?, ?, 25, 'x', 'y', '{}')
                    """,
                    (
                        (
                            "2026-09-23T00:"
                            f"{index:02d}:00+00:00"
                        ),
                        pool,
                        value + pool_index * 10,
                    ),
                )

    report = evaluate_phase9_research(
        storage,
        pool_addresses=pools,
        criteria=Phase9ResearchCriteria(
            min_pools=2,
            min_qualified_pools=2,
            min_qualified_pool_rate=1.0,
            min_mean_survival_uplift_vs_fixed=0.25,
            max_mean_width_multiple_vs_fixed=5.0,
        ),
        adaptive_criteria=AdaptiveRangeCriteria(
            lookback_observations=20,
            holding_observations=2,
            target_coverage=0.80,
            min_half_width_bins=1,
            max_half_width_bins=6,
            min_historical_windows=4,
        ),
        adaptive_validation_criteria=(
            AdaptiveRangeValidationCriteria(
                fixed_half_width_bins=1,
                min_decisions=8,
                min_adaptive_survival_rate=0.75,
                min_survival_uplift_vs_fixed=0.25,
                max_mean_width_multiple_vs_fixed=5.0,
                max_cap_exceeded_rate=0.0,
            )
        ),
        regime_criteria=DLMMRegimeCriteria(
            lookback_observations=24,
            recent_observations=6,
            min_observations=12,
            trend_efficiency_threshold=0.65,
            activity_percentile=0.75,
            quiet_percentile=0.25,
        ),
        as_of="2026-09-23T00:23:00+00:00",
    )
    assert report.research_qualified is True
    persist_phase9_research(storage, report=report)


def seed_static_hedge_lineage(storage, pool):
    observations = (
        ("2026-09-23T10:00:00+00:00", 1 << 64),
        ("2026-09-23T11:00:00+00:00", 1 << 64),
        ("2026-09-23T12:00:00+00:00", 1 << 64),
    )
    with storage.connect() as conn:
        existing = conn.execute(
            """
            SELECT id, observed_at, active_bin_id
            FROM chain_pool_snapshots
            WHERE pool_address = ?
            """,
            (pool,),
        ).fetchall()
        existing_times = {str(row[1]) for row in existing}
        for observed_at, price in observations:
            if observed_at not in existing_times:
                cursor = conn.execute(
                    """
                    INSERT INTO chain_pool_snapshots(
                        observed_at, pool_address, active_bin_id,
                        bin_step, token_x_mint, token_y_mint,
                        token_x_program, token_y_program, raw_json
                    ) VALUES (
                        ?, ?, 0, 25, ?, ?, ?, ?, '{}'
                    )
                    """,
                    (
                        observed_at,
                        pool,
                        f"{pool}-x",
                        f"{pool}-y",
                        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    ),
                )
                assert cursor.lastrowid is not None

            conn.execute(
                """
                INSERT INTO bin_liquidity_snapshots(
                    observed_at, pool_address, bin_array_index,
                    bin_id, price, amount_x, amount_y,
                    liquidity_supply, fee_amount_x_per_token_stored,
                    fee_amount_y_per_token_stored
                ) VALUES (
                    ?, ?, 0, 0, ?, '1', '1', '1', '0', '0'
                )
                """,
                (observed_at, pool, str(price)),
            )

    report = research_static_inventory_hedge(
        storage,
        pool_address=pool,
        amount_x=100,
        amount_y=100,
        instrument=HedgeInstrumentAssumptions(
            instrument_id=f"hedge-{pool}",
            venue="TEST",
            available_liquidity_y_atomic=1_000_000,
            max_liquidity_share_bps=10_000,
            max_leverage=1.0,
            funding_bps_per_holding_window=0.0,
        ),
        criteria=StaticHedgeCriteria(
            observation_limit=3,
            holding_observations=1,
            hedge_fraction=1.0,
            hedge_round_trip_cost_bps=0.0,
            min_windows=1,
            min_mean_abs_return_reduction_bps=0.0,
            min_worst_loss_improvement_bps=0.0,
            max_mean_return_drag_bps=100.0,
        ),
        as_of="2026-09-23T12:00:00+00:00",
    )
    assert report.research_qualified is True
    persist_static_hedge_research(storage, report=report)


def seed_ready(storage):
    promote_phase8(storage)
    portfolio_lineage = seed_portfolio_candidate_lineage(storage)
    seed_bandit_dataset_lineage(storage)
    for pool in ("pool-a", "pool-b"):
        seed_mint_risk_lineage(storage, pool)
        seed_wallet_flow_lineage(storage, pool)
    seed_adaptive_multi_pool_lineage(
        storage,
        ("pool-a", "pool-b"),
    )
    seed_static_hedge_lineage(storage, "pool-a")


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
    storage.save_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
        as_of="2026-09-23T12:30:00+00:00",
        status="NOT_QUALIFIED",
        qualified=False,
        evidence={
            "research_only": True,
            "policy_actionable": False,
        },
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
    assert report.persisted_bundle_hash_valid is True
    assert report.persisted_bundle_matches_current is True
    assert report.persisted_bundle_sha256 == report.research_bundle_sha256
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



def test_phase9_promotion_rejects_tampered_bundle_payload(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    bundle_id = persist_phase9_research_bundle(
        storage,
        report=bundle,
    )

    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE,
        pool_address="__PHASE9_RESEARCH__",
    )
    assert latest is not None
    assert latest["id"] == bundle_id
    persisted = dict(latest["evidence"])
    expected_sha = persisted["bundle_sha256"]
    payload = {
        key: value
        for key, value in persisted.items()
        if key != "bundle_sha256"
    }
    assert phase9_research_bundle_sha256(payload) == expected_sha

    payload["status"] = "TAMPERED_READY"
    tampered = {
        **payload,
        "bundle_sha256": expected_sha,
    }
    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE,
        pool_address="__PHASE9_RESEARCH__",
        as_of="2026-09-23T12:31:00+00:00",
        status="RESEARCH_BUNDLE_READY",
        qualified=True,
        evidence=tampered,
    )

    report = evaluate_phase9_promotion(storage)

    assert report.promotion_ready is False
    assert report.persisted_bundle_hash_valid is False
    assert any(
        "checksum is invalid" in reason
        for reason in report.reasons
    )

def test_persisted_phase9_promotion_audit_is_current_after_persist(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(storage, report=bundle)
    report = evaluate_phase9_promotion(storage)
    assert report.promotion_ready is True
    persist_phase9_promotion(storage, report=report)

    audit = audit_persisted_phase9_promotion(storage)

    assert audit.current is True
    assert audit.history_exists is True
    assert audit.current_row_matches_latest_history is True
    assert audit.persisted_matches_current is True
    assert audit.reasons == ()



def test_phase9_promotion_audit_rejects_current_row_tampering(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(storage, report=bundle)
    report = evaluate_phase9_promotion(storage)
    assert report.promotion_ready is True
    persist_phase9_promotion(storage, report=report)

    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE phase_promotion_evidence
            SET evidence_json = '{}'
            WHERE phase_name = 'PHASE9'
            """
        )

    audit = audit_persisted_phase9_promotion(storage)

    assert audit.current is False
    assert audit.history_exists is True
    assert audit.current_row_matches_latest_history is False
    assert any(
        "differs from immutable history" in reason
        for reason in audit.reasons
    )
    history = storage.phase_promotion_history("PHASE9")
    assert len(history) == 1
    assert history[0]["qualified"] is True

def test_persisted_phase9_promotion_audit_detects_staleness(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(storage, report=bundle)
    report = evaluate_phase9_promotion(storage)
    assert report.promotion_ready is True
    persist_phase9_promotion(storage, report=report)

    seed_mint_risk_lineage(storage, "pool-c")

    audit = audit_persisted_phase9_promotion(storage)

    assert audit.current is False
    assert audit.current_promotion_ready is False
    assert any(
        "current Phase 9 promotion gate no longer passes" in reason
        or "stale versus current bundle" in reason
        for reason in audit.reasons
    )

def test_phase9_promotion_requires_complete_research_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    storage.save_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:30:00+00:00",
        status="NOT_QUALIFIED",
        qualified=False,
        evidence={
            "research_only": True,
            "policy_actionable": False,
        },
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

    seed_mint_risk_lineage(storage, "pool-c")

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


def test_forged_portfolio_candidate_lineage_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["candidate_lineage"] = {
        **forged["candidate_lineage"],
        "candidate_evidence_id": 999999,
    }
    storage.save_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
        as_of="2026-09-23T12:02:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "immutable candidate-artifact lineage" in reason
        for reason in report.reasons
    )



def test_forged_portfolio_allocation_metrics_block_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    allocations = [dict(item) for item in forged["allocations"]]
    allocations[0]["allocation_quote"] = 99.0
    forged["allocations"] = allocations
    storage.save_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
        as_of="2026-09-23T12:02:30+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "immutable candidate-artifact lineage" in reason
        for reason in report.reasons
    )

def test_forged_mint_snapshot_lineage_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["assessments"] = [
        {
            **item,
            "mint_snapshot_id": 999999,
        }
        for item in forged["assessments"]
    ]
    storage.save_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:03:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "authoritative pool and mint snapshot IDs" in reason
        for reason in report.reasons
    )



def test_tampered_mint_snapshot_payload_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    mint_id = int(
        latest["evidence"]["assessments"][0]["mint_snapshot_id"]
    )

    with storage.connect() as conn:
        # Bypass the storage-layer trigger to prove the hash/replay gate
        # independently detects source corruption.
        conn.execute("DROP TRIGGER token_mint_snapshots_no_update")
        conn.execute(
            """
            UPDATE token_mint_snapshots
            SET mint_authority = 'tampered-authority'
            WHERE id = ?
            """,
            (mint_id,),
        )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "authoritative pool and mint snapshot IDs" in reason
        for reason in report.reasons
    )


def test_tampered_pool_snapshot_payload_blocks_mint_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    pool_snapshot_id = int(
        latest["evidence"]["pool_snapshot_id"]
    )

    with storage.connect() as conn:
        # Bypass the storage-layer trigger to prove the hash/replay gate
        # independently detects source corruption.
        conn.execute("DROP TRIGGER chain_pool_snapshots_no_update")
        conn.execute(
            """
            UPDATE chain_pool_snapshots
            SET bin_step = bin_step + 1
            WHERE id = ?
            """,
            (pool_snapshot_id,),
        )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "authoritative pool and mint snapshot IDs" in reason
        for reason in report.reasons
    )

def test_forged_wallet_flow_lineage_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["source_event_sha256"] = "0" * 64
    storage.save_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:04:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "position-event IDs with matching source hash" in reason
        for reason in report.reasons
    )


def test_forged_adaptive_snapshot_lineage_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    pools = [dict(item) for item in forged["pools"]]
    pools[0] = {
        **pools[0],
        "adaptive": {
            **pools[0]["adaptive"],
            "source_snapshot_ids": [999999],
        },
    }
    forged["pools"] = pools
    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
        as_of="2026-09-23T12:04:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "immutable chain snapshot IDs" in reason
        for reason in report.reasons
    )



def test_forged_adaptive_metrics_block_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    pools = [dict(item) for item in forged["pools"]]
    first = dict(pools[0])
    adaptive = dict(first["adaptive"])
    adaptive["survival_uplift_vs_fixed"] = 999.0
    first["adaptive"] = adaptive
    pools[0] = first
    forged["pools"] = pools
    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
        as_of="2026-09-23T00:24:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "immutable chain snapshot IDs" in reason
        for reason in report.reasons
    )

def test_forged_static_hedge_lineage_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    observations = [
        dict(item) for item in forged["source_observations"]
    ]
    observations[0]["bin_liquidity_snapshot_id"] = 999999
    forged["source_observations"] = observations
    storage.save_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:08:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "pool/bin price-path IDs" in reason
        for reason in report.reasons
    )



def test_tampered_static_hedge_assumptions_block_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    tampered = dict(latest["evidence"])
    tampered["instrument"] = {
        **tampered["instrument"],
        "funding_bps_per_holding_window": 500.0,
    }
    storage.save_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:09:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=tampered,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "pool/bin price-path IDs" in reason
        for reason in report.reasons
    )

def test_portfolio_candidate_evidence_is_immutable(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest_allocation = storage.latest_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
    )
    assert latest_allocation is not None
    lineage = latest_allocation["evidence"]["candidate_lineage"]
    evidence_id = int(lineage["candidate_evidence_id"])

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT evidence_json
            FROM advanced_edge_evidence
            WHERE id = ?
            """,
            (evidence_id,),
        ).fetchone()
        assert row is not None
        try:
            conn.execute(
                """
                UPDATE advanced_edge_evidence
                SET evidence_json = '{}'
                WHERE id = ?
                """,
                (evidence_id,),
            )
        except sqlite3.IntegrityError as exc:
            assert "immutable" in str(exc)
        else:
            raise AssertionError(
                "expected immutable advanced-edge evidence update refusal"
            )

def test_tampered_bandit_dataset_file_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
    )
    assert latest is not None
    lineage = latest["evidence"]["dataset_lineage"]
    dataset_path = Path(str(lineage["output_file"]))
    assert dataset_path.is_file()

    dataset_path.write_text(
        dataset_path.read_text(encoding="utf-8")
        + "2026-09-23T11:00:00+00:00,"
        "2026-09-23T11:30:00+00:00\n",
        encoding="utf-8",
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "checksum-verified retraining dataset lineage" in reason
        for reason in report.reasons
    )


def test_forged_mint_assessment_facts_block_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    assessments = [
        dict(item) for item in forged["assessments"]
    ]
    assessments[0]["decimals"] = int(assessments[0]["decimals"]) + 1
    forged["assessments"] = assessments
    storage.save_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:10:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "authoritative pool and mint snapshot IDs" in reason
        for reason in report.reasons
    )


def test_forged_wallet_flow_metric_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["total_activity_usd"] = (
        float(forged["total_activity_usd"]) + 1.0
    )
    storage.save_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:11:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "immutable position-event IDs" in reason
        for reason in report.reasons
    )


def test_forged_adaptive_metric_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["qualified_pool_rate"] = 0.5
    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
        as_of="2026-09-23T12:12:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "immutable chain snapshot IDs" in reason
        for reason in report.reasons
    )


def test_forged_bandit_metric_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["mean_selected_reward_bps"] = (
        float(forged["mean_selected_reward_bps"]) + 1.0
    )
    storage.save_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
        as_of="2026-09-23T12:13:00+00:00",
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
