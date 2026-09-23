import hashlib
from pathlib import Path
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
    Phase9ResearchBundleCriteria,
    evaluate_phase9_research_bundle,
    persist_phase9_research_bundle,
)
from meteora_learner.phase9_work_queue import build_phase9_work_queue
from meteora_learner.phase_promotion import (
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
    persist_phase9_promotion,
)
from meteora_learner.portfolio_allocation import (
    PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
    PortfolioAllocationCriteria,
    persist_portfolio_allocation_research,
    persist_portfolio_candidate_research,
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


def save_pool(storage, pool, observed_at):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": pool,
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": f"{pool}-x",
            "token_y_mint": f"{pool}-y",
            "bin_arrays": [],
        },
        observed_at=observed_at,
    )


def save_mint_snapshot(storage, mint):
    storage.save_token_mint_snapshot(
        {
            "mint_address": mint,
            "token_program": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
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
        observed_at="2026-09-23T12:00:00+00:00",
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


def seed_retraining_dataset_evidence(
    storage,
    *,
    cycle_id="cycle-lineage",
    cutoff="2026-09-23T12:00:00+00:00",
):
    dataset_path = (
        Path(storage.path).parent
        / f"{cycle_id}-retrain.csv"
    )
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
                ?, '2026-09-23T00:00:00+00:00',
                '2026-09-23T00:00:00+00:00',
                'PLANNED', NULL, 'champion', 'dataset-v1',
                '2026-09-23T00:00:00+00:00', 1,
                ?, ?, NULL, '{}', NULL
            )
            """,
            (cycle_id, cutoff, dataset_version),
        )
    evidence_id = storage.save_model_live_evidence(
        model_id="champion",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
        status="BUILT",
        evidence={
            "cycle_id": cycle_id,
            "cutoff": cutoff,
            "target_dataset_version": dataset_version,
            "dataset": {
                "dataset_sha256": digest,
                "dataset_version": dataset_version,
            },
            "output_file": str(dataset_path),
        },
    )
    return {
        "cycle_id": cycle_id,
        "champion_model_id": "champion",
        "dataset_evidence_id": evidence_id,
        "dataset_version": dataset_version,
        "dataset_sha256": digest,
        "cutoff": cutoff,
        "output_file": str(dataset_path),
    }


def promote_phase8(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def evidence(storage, edge_type, pool, *, extra=None):
    return storage.save_advanced_edge_evidence(
        edge_type=edge_type,
        pool_address=pool,
        as_of="2026-09-23T12:00:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_qualified": True,
            "research_only": True,
            "policy_actionable": False,
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
    persist_pool_mint_risk(storage, report=report)


def seed_adaptive_multi_pool_lineage(storage, pools):
    token_program = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    with storage.connect() as conn:
        for pool in pools:
            for index in range(10):
                observed_at = (
                    f"2026-09-23T11:{50 + index:02d}:00+00:00"
                )
                conn.execute(
                    """
                    INSERT INTO chain_pool_snapshots(
                        observed_at, pool_address, active_bin_id,
                        bin_step, token_x_mint, token_y_mint,
                        token_x_program, token_y_program, raw_json
                    ) VALUES (?, ?, ?, 25, ?, ?, ?, ?, '{}')
                    """,
                    (
                        observed_at,
                        pool,
                        index % 2,
                        f"{pool}-x",
                        f"{pool}-y",
                        token_program,
                        token_program,
                    ),
                )

    report = evaluate_phase9_research(
        storage,
        pool_addresses=pools,
        criteria=Phase9ResearchCriteria(
            min_pools=len(pools),
            min_qualified_pools=len(pools),
            min_qualified_pool_rate=1.0,
            min_mean_survival_uplift_vs_fixed=-1.0,
            max_mean_width_multiple_vs_fixed=10.0,
        ),
        adaptive_criteria=AdaptiveRangeCriteria(
            lookback_observations=8,
            holding_observations=1,
            target_coverage=0.5,
            min_half_width_bins=1,
            max_half_width_bins=10,
            min_historical_windows=2,
        ),
        adaptive_validation_criteria=AdaptiveRangeValidationCriteria(
            fixed_half_width_bins=5,
            min_decisions=2,
            min_adaptive_survival_rate=0.0,
            min_survival_uplift_vs_fixed=-1.0,
            max_mean_width_multiple_vs_fixed=10.0,
            max_cap_exceeded_rate=1.0,
        ),
        regime_criteria=DLMMRegimeCriteria(
            lookback_observations=8,
            recent_observations=2,
            min_observations=3,
            trend_efficiency_threshold=0.65,
            activity_percentile=0.75,
            quiet_percentile=0.25,
        ),
        as_of="2026-09-23T12:00:00+00:00",
    )
    assert report.research_qualified is True
    persist_phase9_research(storage, report=report)


def seed_static_hedge_lineage(storage, pool):
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT observed_at, active_bin_id
            FROM chain_pool_snapshots
            WHERE pool_address = ?
            ORDER BY julianday(observed_at) ASC, id ASC
            """,
            (pool,),
        ).fetchall()
        assert rows
        for index, row in enumerate(rows):
            observed_at = str(row[0])
            active_bin_id = int(row[1])
            conn.execute(
                """
                INSERT INTO bin_liquidity_snapshots(
                    observed_at, pool_address, bin_array_index,
                    bin_id, price, amount_x, amount_y,
                    liquidity_supply, fee_amount_x_per_token_stored,
                    fee_amount_y_per_token_stored
                ) VALUES (?, ?, 0, ?, ?, '1', '1', '1', '0', '0')
                """,
                (
                    observed_at,
                    pool,
                    active_bin_id,
                    str((1 << 64) + index * (1 << 58)),
                ),
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
            observation_limit=10,
            holding_observations=1,
            hedge_fraction=1.0,
            hedge_round_trip_cost_bps=0.0,
            min_windows=1,
            min_mean_abs_return_reduction_bps=0.0,
            min_worst_loss_improvement_bps=0.0,
            max_mean_return_drag_bps=10_000.0,
        ),
        as_of="2026-09-23T12:00:00+00:00",
    )
    assert report.research_qualified is True
    persist_static_hedge_research(storage, report=report)


def seed_ready(storage):
    promote_phase8(storage)
    portfolio_lineage = seed_portfolio_candidate_lineage(storage)
    seed_retraining_dataset_evidence(
        storage,
        cycle_id="cycle",
    )
    bandit = evaluate_cycle_contextual_bandit(
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
    assert bandit.report.research_qualified is True
    persist_cycle_contextual_bandit(storage, result=bandit)
    for pool in ("pool-a", "pool-b"):
        seed_mint_risk_lineage(storage, pool)
        seed_wallet_flow_lineage(storage, pool)
    seed_adaptive_multi_pool_lineage(
        storage,
        ("pool-a", "pool-b"),
    )
    seed_static_hedge_lineage(storage, "pool-a")


def test_work_queue_surfaces_concrete_missing_research(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for index, pool in enumerate(("pool-a", "pool-b", "pool-c")):
        save_pool(
            storage,
            pool,
            f"2026-09-23T1{index}:00:00+00:00",
        )

    queue = build_phase9_work_queue(storage)

    task_types = {item.task_type for item in queue.items}
    assert "PHASE8_PROMOTION_REQUIRED" in task_types
    assert "ADAPTIVE_MULTI_POOL" in task_types
    assert "MINT_SNAPSHOT" in task_types
    assert "WALLET_FLOW" in task_types
    assert "STATIC_HEDGE" in task_types
    assert "PORTFOLIO_ALLOCATION" in task_types
    assert "CONTEXTUAL_BANDIT" in task_types
    adaptive = next(
        item for item in queue.items
        if item.task_type == "ADAPTIVE_MULTI_POOL"
    )
    assert adaptive.shell_command is not None
    assert "phase9-research-validate" in adaptive.shell_command
    assert queue.candidate_pools == ("pool-a", "pool-b", "pool-c")


def test_work_queue_requests_bundle_refresh_after_new_evidence(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(storage, report=bundle)

    seed_mint_risk_lineage(storage, "pool-c")

    queue = build_phase9_work_queue(storage)

    refresh = [
        item for item in queue.items
        if item.task_type == "REFRESH_RESEARCH_BUNDLE"
    ]
    assert len(refresh) == 1
    assert "phase9-research-bundle" in refresh[0].shell_command
    assert queue.promotion_ready is False


def test_work_queue_surfaces_promotion_when_bundle_is_current(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(storage, report=bundle)

    queue = build_phase9_work_queue(storage)

    promotion = [
        item for item in queue.items
        if item.task_type == "PERSIST_PHASE9_PROMOTION"
    ]
    assert len(promotion) == 1
    assert "phase9-validate" in promotion[0].shell_command
    assert queue.research_bundle_ready is True
    assert queue.promotion_ready is True



def test_work_queue_suppresses_current_phase9_promotion_task(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(storage, report=bundle)
    from meteora_learner.phase9_validation import evaluate_phase9_promotion

    report = evaluate_phase9_promotion(storage)
    assert report.promotion_ready is True
    persist_phase9_promotion(storage, report=report)

    queue = build_phase9_work_queue(storage)

    assert queue.promotion_ready is True
    assert not any(
        item.task_type == "PERSIST_PHASE9_PROMOTION"
        for item in queue.items
    )

def test_work_queue_advances_to_mint_risk_after_snapshots(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for index, pool in enumerate(("pool-a", "pool-b", "pool-c")):
        save_pool(
            storage,
            pool,
            f"2026-09-23T1{index}:00:00+00:00",
        )
        save_mint_snapshot(storage, f"{pool}-x")
        save_mint_snapshot(storage, f"{pool}-y")

    queue = build_phase9_work_queue(
        storage,
        rpc_url="https://rpc.example.invalid",
    )

    task_types = {item.task_type for item in queue.items}
    assert "MINT_SNAPSHOT" not in task_types
    assert "MINT_RISK" in task_types
    mint_task = next(
        item for item in queue.items
        if item.task_type == "MINT_RISK"
    )
    assert mint_task.shell_command is not None
    assert "mint-risk-research" in mint_task.shell_command


def test_work_queue_mint_snapshot_command_uses_requested_rpc(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(
        storage,
        "pool-a",
        "2026-09-23T10:00:00+00:00",
    )

    queue = build_phase9_work_queue(
        storage,
        criteria=Phase9ResearchBundleCriteria(
            min_mint_risk_pools=1,
            min_wallet_flow_pools=1,
            min_static_hedge_pools=1,
        ),
        rpc_url="https://rpc.example.invalid",
    )

    task = next(
        item for item in queue.items
        if item.task_type == "MINT_SNAPSHOT"
    )
    assert task.shell_command is not None
    assert "https://rpc.example.invalid" in task.shell_command
    assert "inspect-mint" in task.shell_command
    assert "mint-snapshot-ingest" in task.shell_command


def test_work_queue_prefers_cycle_bound_bandit_when_lineage_exists(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_retraining_dataset_evidence(storage)

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "CONTEXTUAL_BANDIT"
    )
    assert task.scope == "cycle-lineage"
    assert task.shell_command is not None
    assert "contextual-bandit-cycle-research" in task.shell_command
    assert "--cycle-id cycle-lineage" in task.shell_command


def test_work_queue_repairs_invalid_wallet_flow_lineage(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    forged = dict(latest["evidence"])
    forged["source_event_sha256"] = "0" * 64
    storage.save_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:05:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "WALLET_FLOW_REPAIR"
        and item.scope == "pool-a"
    )
    assert task.shell_command is not None
    assert "wallet-flow-research --pool pool-a" in task.shell_command


def test_work_queue_repairs_invalid_bandit_lineage(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
    )
    forged = dict(latest["evidence"])
    forged["dataset_lineage"] = {
        **forged["dataset_lineage"],
        "dataset_evidence_id": 999999,
    }
    storage.save_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
        as_of="2026-09-23T12:06:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "CONTEXTUAL_BANDIT_REPAIR"
    )
    assert task.scope == "cycle"
    assert task.shell_command is not None
    assert "contextual-bandit-cycle-research" in task.shell_command


def test_work_queue_repairs_invalid_adaptive_lineage(tmp_path):
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
        "regime": {
            **pools[0]["regime"],
            "source_snapshot_sha256": "0" * 64,
        },
    }
    forged["pools"] = pools
    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
        as_of="2026-09-23T12:07:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "ADAPTIVE_MULTI_POOL_REPAIR"
    )
    assert task.scope == "__MULTI_POOL__"
    assert task.shell_command is not None
    assert "phase9-research-validate" in task.shell_command


def test_work_queue_repairs_invalid_static_hedge_lineage(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["source_path_sha256"] = "0" * 64
    storage.save_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:09:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "STATIC_HEDGE_REPAIR"
        and item.scope == "pool-a"
    )
    assert task.shell_command is None
    assert "original explicit instrument" in task.reason
