import json
import hashlib
from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace
import meteora_learner.phase9_work_queue as work_queue_module
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
from meteora_learner.phase8_validation import (
    Phase8PromotionCriteria,
    evaluate_phase8_promotion,
)
from meteora_learner.mint_risk import (
    MINT_RISK_EVIDENCE_TYPE,
    persist_pool_mint_risk,
    research_pool_mint_risk,
)
from meteora_learner.phase9_explicit_inputs import (
    PHASE9_EXPLICIT_INPUTS_EVIDENCE_TYPE,
    PHASE9_EXPLICIT_INPUTS_SCOPE,
    parse_phase9_explicit_inputs,
    persist_phase9_explicit_inputs,
)
from meteora_learner.phase9_pool_activity_scan_state import (
    record_phase9_pool_activity_page,
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
from meteora_learner.phase9_work_queue import (
    build_phase9_work_queue,
    persist_phase9_work_queue_snapshot,
)
from meteora_learner.phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
    persist_phase8_promotion,
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


def seed_explicit_inputs(storage):
    payload = {
        "static_hedges": [
            {
                "pool_address": "pool-a",
                "amount_x": 100,
                "amount_y": 100,
                "instrument": {
                    "instrument_id": "SOL-PERP",
                    "venue": "TEST",
                    "available_liquidity_y_atomic": 1_000_000,
                    "max_liquidity_share_bps": 1000,
                    "max_leverage": 1.0,
                    "funding_bps_per_holding_window": 0.0,
                },
                "criteria": {
                    "observation_limit": 96,
                    "holding_observations": 6,
                    "hedge_fraction": 1.0,
                    "hedge_round_trip_cost_bps": 10.0,
                    "min_windows": 20,
                    "min_mean_abs_return_reduction_bps": 0.0,
                    "min_worst_loss_improvement_bps": 0.0,
                    "max_mean_return_drag_bps": 100.0,
                },
                "as_of": None,
            }
        ],
        "pool_inputs": [
            {
                "pool_address": "pool-a",
                "amount_x": 100,
                "amount_y": 100,
                "requested_quote": 100.0,
                "network_cost_y_atomic": 1000,
            },
            {
                "pool_address": "pool-b",
                "amount_x": 100,
                "amount_y": 100,
                "requested_quote": 100.0,
                "network_cost_y_atomic": 1000,
            },
        ],
        "portfolio": {
            "account_equity_quote": 1000.0,
            "cash_quote": 1000.0,
            "current_deployed_quote": 0.0,
            "portfolio_drawdown_bps": 0,
            "observation_limit": 12,
            "half_widths": [0, 1, 2, 5, 10],
            "center_offsets": [0],
            "strategies": ["SPOT", "CURVE", "BID_ASK"],
            "max_share_bps": 500,
            "favor_x_in_active_bin": False,
            "budget_quote": 200.0,
            "allocation_criteria": {
                "max_positions": 3,
                "min_positions": 2,
                "max_pool_allocation_bps": 5000,
                "min_range_survival_ratio": 0.75,
                "min_excess_vs_hold_bps": 0,
                "min_position_quote": 10.0,
                "min_budget_utilization_rate": 0.75,
            },
        },
    }
    return persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(payload),
    )


def seed_bandit_explicit_inputs(storage):
    base = seed_explicit_inputs(storage)
    payload = json.loads(json.dumps(base.inputs.to_record()))
    payload["pool_inputs"].append(
        {
            "pool_address": "pool-c",
            "amount_x": 100,
            "amount_y": 100,
            "requested_quote": 100.0,
            "network_cost_y_atomic": 1000,
        }
    )
    return persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(payload),
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
        phase_name=PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )
    with storage.connect() as conn:
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
                '2026-09-22T00:00:00+00:00',
                '2026-09-23T00:00:00+00:00',
                'COMPLETED', NULL,
                'old-champion', 'dataset-v0',
                '2026-09-21T00:00:00+00:00',
                1, '2026-09-22T00:00:00+00:00',
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
                'pool-a', 'champion', 'SPOT',
                -1, 1, 3, '10', '1', '1',
                '1', 100, 0, 1, 'USD',
                'phase8-signature', 'phase8-close',
                '2026-09-23T00:00:00+00:00', '{}'
            )
            """
        )
    storage.save_model_live_evidence(
        model_id="champion",
        evidence_type="CONTINUOUS_CHAMPION_PROMOTION_V1",
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
            hedge_fraction=0.0,
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
    seed_retraining_dataset_evidence(
        storage,
        cycle_id="cycle",
    )
    promote_phase8(storage)
    portfolio_lineage = seed_portfolio_candidate_lineage(storage)
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
    assert "CHAIN_HISTORY_DEPTH" in task_types
    assert "MINT_SNAPSHOT" in task_types
    assert "WALLET_FLOW_CAPTURE" in task_types
    assert "WALLET_FLOW" in task_types
    assert "EXPLICIT_RESEARCH_INPUTS" in task_types
    assert "CONTEXTUAL_BANDIT" in task_types
    history = next(
        item for item in queue.items
        if item.task_type == "CHAIN_HISTORY_DEPTH"
    )
    adaptive = next(
        item for item in queue.items
        if item.task_type == "ADAPTIVE_MULTI_POOL"
    )
    assert history.shell_command is not None
    assert "phase9-chain-history-plan" in history.shell_command
    assert "pool-a:42" in history.reason
    assert "pool-b:42" in history.reason
    assert "pool-c:42" in history.reason
    assert adaptive.shell_command is None
    assert "exact chain-history depth" in adaptive.reason
    explicit = next(
        item for item in queue.items
        if item.task_type == "EXPLICIT_RESEARCH_INPUTS"
    )
    assert explicit.shell_command is not None
    assert "phase9-research-input-template" in explicit.shell_command
    assert "phase9-research-inputs.json" in explicit.shell_command
    assert "fill every null economic field" in explicit.reason
    assert queue.candidate_pools == ("pool-a", "pool-b", "pool-c")


def test_work_queue_runs_checksum_bound_explicit_inputs_when_available(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_pool(
            storage,
            pool,
            "2026-09-23T12:00:00+00:00",
        )
    artifact = seed_explicit_inputs(storage)

    queue = build_phase9_work_queue(storage)

    assert not any(
        item.task_type == "EXPLICIT_RESEARCH_INPUTS"
        for item in queue.items
    )
    task = next(
        item for item in queue.items
        if item.task_type == "EXPLICIT_RESEARCH_RUN"
    )
    assert task.scope == str(artifact.evidence_id)
    assert task.shell_command is not None
    assert (
        f"--input-evidence-id {artifact.evidence_id}"
        in task.shell_command
    )
    assert "--persist --require-ready" in task.shell_command


def test_work_queue_repairs_invalid_explicit_input_artifact(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_pool(
            storage,
            pool,
            "2026-09-23T12:00:00+00:00",
        )
    artifact = seed_explicit_inputs(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_EXPLICIT_INPUTS_EVIDENCE_TYPE,
        pool_address=PHASE9_EXPLICIT_INPUTS_SCOPE,
    )
    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_EXPLICIT_INPUTS_EVIDENCE_TYPE,
        pool_address=PHASE9_EXPLICIT_INPUTS_SCOPE,
        as_of=None,
        status="INPUTS_VALIDATED",
        qualified=False,
        evidence={
            "artifact_sha256": "0" * 64,
            "inputs": latest["evidence"]["inputs"],
        },
    )

    queue = build_phase9_work_queue(storage)

    assert not any(
        item.task_type == "EXPLICIT_RESEARCH_RUN"
        and item.scope == str(artifact.evidence_id)
        for item in queue.items
    )
    repair = next(
        item for item in queue.items
        if item.task_type == "EXPLICIT_RESEARCH_INPUTS"
    )
    assert repair.shell_command is not None
    assert "phase9-research-input-template" in repair.shell_command
    assert "latest input artifact is invalid" in repair.reason
    assert "SHA-256 does not match" in repair.reason


def test_work_queue_releases_adaptive_research_after_exact_history_depth(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        for index in range(43):
            save_pool(
                storage,
                pool,
                f"2026-09-23T12:00:{index:02d}+00:00",
            )

    queue = build_phase9_work_queue(storage)

    assert not any(
        item.task_type == "CHAIN_HISTORY_DEPTH"
        for item in queue.items
    )
    adaptive = next(
        item for item in queue.items
        if item.task_type == "ADAPTIVE_MULTI_POOL"
    )
    assert adaptive.shell_command is not None
    assert "phase9-research-validate" in adaptive.shell_command
    assert "pool-a,pool-b,pool-c" in adaptive.shell_command


def test_work_queue_prefers_ranked_pool_over_deeper_chain_pool(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        work_queue_module,
        "utc_now_iso",
        lambda: "2026-09-23T13:00:00+00:00",
    )

    for index in range(3):
        save_pool(
            storage,
            "pool-a",
            f"2026-09-23T10:00:0{index}+00:00",
        )
    save_pool(
        storage,
        "pool-b",
        "2026-09-23T10:00:00+00:00",
    )
    save_pool(
        storage,
        "pool-c",
        "2026-09-23T10:00:00+00:00",
    )
    with storage.connect() as conn:
        for pool, tvl in (
            ("pool-b", 3_000.0),
            ("pool-c", 2_000.0),
            ("pool-a", 1_000.0),
        ):
            conn.execute(
                """
                INSERT INTO pool_snapshots(
                    observed_at, address, name, tvl,
                    volume_24h, fees_24h, raw_json
                ) VALUES (
                    '2026-09-23T12:00:00+00:00',
                    ?, ?, ?, 100, 1, '{}'
                )
                """,
                (pool, pool, tvl),
            )

    queue = build_phase9_work_queue(
        storage,
        criteria=Phase9ResearchBundleCriteria(
            min_mint_risk_pools=1,
            min_wallet_flow_pools=1,
            min_static_hedge_pools=1,
        ),
    )

    mint = next(
        item for item in queue.items
        if item.task_type == "MINT_SNAPSHOT"
    )
    wallet = next(
        item for item in queue.items
        if item.task_type == "WALLET_FLOW_CAPTURE"
    )
    assert mint.scope == "pool-b"
    assert wallet.scope == "pool-b"


def test_work_queue_blocks_wallet_flow_until_source_thresholds(
    tmp_path,
):
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

    capture = next(
        item for item in queue.items
        if item.task_type == "WALLET_FLOW_CAPTURE"
    )
    research = next(
        item for item in queue.items
        if item.task_type == "WALLET_FLOW"
        and item.scope == "pool-a"
    )
    assert capture.scope == "pool-a"
    assert capture.shell_command is not None
    assert (
        "SOLANA_RPC_URL=https://rpc.example.invalid"
        in capture.shell_command
    )
    assert "phase9-wallet-flow-capture-run" in capture.shell_command
    assert "--pool pool-a" in capture.shell_command
    assert "events 0/20" in capture.reason
    assert "unique users 0/5" in capture.reason
    assert "historical pool-signature backfill has not started" in (
        capture.reason
    )
    assert research.shell_command is None
    assert "blocked until real position-history" in research.reason


def test_work_queue_reports_wallet_history_backfill_progress(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    save_pool(
        storage,
        "pool-a",
        "2026-09-23T10:00:00+00:00",
    )
    criteria = Phase9ResearchBundleCriteria(
        min_mint_risk_pools=1,
        min_wallet_flow_pools=1,
        min_static_hedge_pools=1,
    )
    record_phase9_pool_activity_page(
        storage,
        pool_address="pool-a",
        next_before_signature="cursor-1",
        has_more=True,
        signatures_scanned=25,
        matching_transactions=4,
        positions_discovered=3,
    )

    queue = build_phase9_work_queue(
        storage,
        criteria=criteria,
    )
    capture = next(
        item for item in queue.items
        if item.task_type == "WALLET_FLOW_CAPTURE"
    )
    assert "backfill is in progress" in capture.reason
    assert "1 page(s), 25 signature(s), 3 position candidate(s)" in (
        capture.reason
    )

    record_phase9_pool_activity_page(
        storage,
        pool_address="pool-a",
        next_before_signature="cursor-end",
        has_more=False,
        signatures_scanned=7,
        matching_transactions=1,
        positions_discovered=1,
    )
    queue = build_phase9_work_queue(
        storage,
        criteria=criteria,
    )
    capture = next(
        item for item in queue.items
        if item.task_type == "WALLET_FLOW_CAPTURE"
    )
    assert "backfill is exhausted" in capture.reason
    assert "2 page(s) and 32 signature(s)" in capture.reason
    assert "recent live rescans remain available" in capture.reason


def test_work_queue_unlocks_wallet_flow_after_real_source_thresholds(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    save_pool(
        storage,
        "pool-a",
        "2026-09-23T10:00:00+00:00",
    )
    with storage.connect() as conn:
        for user_index in range(5):
            for event_index in range(4):
                value = user_index * 10 + event_index
                conn.execute(
                    """
                    INSERT INTO position_event_history(
                        observed_at, position_address, signature, ix_index,
                        event_type, block_time, slot, pool_address,
                        user_address, token_x, token_y,
                        amount_x, amount_y, amount_x_usd, amount_y_usd,
                        total_usd, created_at, raw_json
                    ) VALUES (
                        ?, ?, ?, 0, 'ADD_LIQUIDITY', ?, ?, ?,
                        ?, 'X', 'Y', '1', '1', '1', '1',
                        '2', ?, '{}'
                    )
                    """,
                    (
                        "2026-09-23T13:00:00+00:00",
                        f"position-{user_index}",
                        f"sig-{value}",
                        1_795_000_000 + value,
                        1000 + value,
                        "pool-a",
                        f"user-{user_index}",
                        f"2026-09-23T12:00:{value:02d}+00:00",
                    ),
                )

    queue = build_phase9_work_queue(
        storage,
        criteria=Phase9ResearchBundleCriteria(
            min_mint_risk_pools=1,
            min_wallet_flow_pools=1,
            min_static_hedge_pools=1,
        ),
        as_of="2026-09-23T13:00:00+00:00",
    )

    assert not any(
        item.task_type == "WALLET_FLOW_CAPTURE"
        for item in queue.items
    )
    research = next(
        item for item in queue.items
        if item.task_type == "WALLET_FLOW"
        and item.scope == "pool-a"
    )
    assert research.shell_command is not None
    assert "wallet-flow-research --pool pool-a" in research.shell_command
    assert "--as-of 2026-09-23T13:00:00+00:00" in (
        research.shell_command
    )


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
        as_of="2026-09-23T13:00:00+00:00",
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
    assert task.scope == "pool-a"
    assert "SOLANA_RPC_URL=https://rpc.example.invalid" in task.shell_command
    assert "phase9-mint-capture-run" in task.shell_command
    assert "--target-pools 1" in task.shell_command
    assert "--pools pool-a" in task.shell_command
    assert "--require-ready" in task.shell_command
    assert "inspect-mint" not in task.shell_command


def test_work_queue_mint_snapshot_defaults_to_env_rpc(tmp_path):
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
    )

    task = next(
        item for item in queue.items
        if item.task_type == "MINT_SNAPSHOT"
    )
    assert task.shell_command is not None
    assert task.scope == "pool-a"
    assert "phase9-mint-capture-run" in task.shell_command
    assert "--pools pool-a" in task.shell_command
    assert "SOLANA_RPC_URL=" not in task.shell_command
    assert "<RPC_URL>" not in task.shell_command


def test_work_queue_refreshes_stale_live_mint_snapshots(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(
        storage,
        "pool-a",
        "2026-09-23T10:00:00+00:00",
    )
    stale_at = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat()
    for mint in ("pool-a-x", "pool-a-y"):
        storage.save_token_mint_snapshot(
            {
                "mint_address": mint,
                "token_program": (
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
                ),
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
            observed_at=stale_at,
        )

    queue = build_phase9_work_queue(
        storage,
        criteria=Phase9ResearchBundleCriteria(
            min_mint_risk_pools=1,
            min_wallet_flow_pools=1,
            min_static_hedge_pools=1,
        ),
    )

    task = next(
        item for item in queue.items
        if item.task_type == "MINT_SNAPSHOT"
    )
    assert task.scope == "pool-a"
    assert task.shell_command is not None
    assert "phase9-mint-capture-run" in task.shell_command
    assert "exceeds 3600s" in task.reason


def test_work_queue_refuses_historical_mint_backfill(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(
        storage,
        "pool-a",
        "2026-09-23T10:00:00+00:00",
    )
    save_mint_snapshot(storage, "pool-a-x")
    save_mint_snapshot(storage, "pool-a-y")

    queue = build_phase9_work_queue(
        storage,
        criteria=Phase9ResearchBundleCriteria(
            min_mint_risk_pools=1,
            min_wallet_flow_pools=1,
            min_static_hedge_pools=1,
        ),
        as_of="2026-09-23T13:00:01+00:00",
    )

    task = next(
        item for item in queue.items
        if item.task_type == "MINT_SNAPSHOT"
    )
    assert task.scope == "pool-a"
    assert task.shell_command is None
    assert "cannot backfill" in task.reason
    assert "exceeds 3600s" in task.reason


def test_work_queue_derives_bandit_dataset_from_explicit_inputs(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    artifact = seed_bandit_explicit_inputs(storage)

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "CONTEXTUAL_BANDIT"
    )
    assert task.scope == str(artifact.evidence_id)
    assert task.shell_command is not None
    assert "phase9-bandit-research-run" in task.shell_command
    assert (
        f"--input-evidence-id {artifact.evidence_id}"
        in task.shell_command
    )
    assert "--persist --require-qualified" in task.shell_command
    assert "derive" in task.reason


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
        if item.task_type == "EXPLICIT_RESEARCH_INPUTS_REPAIR"
    )
    assert task.scope == "USER_ASSUMPTIONS_REQUIRED"
    assert task.shell_command is not None
    assert "phase9-research-input-template" in task.shell_command
    assert "static hedge" in task.reason
    assert "invalid lineage" in task.reason



def test_work_queue_repairs_invalid_static_hedge_from_explicit_artifact(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    artifact = seed_explicit_inputs(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
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
        if item.task_type == "EXPLICIT_RESEARCH_REPAIR"
    )
    assert task.scope == str(artifact.evidence_id)
    assert task.shell_command is not None
    assert (
        f"--input-evidence-id {artifact.evidence_id}"
        in task.shell_command
    )
    assert "--persist --require-ready" in task.shell_command


def test_work_queue_surfaces_storage_integrity_first(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        conn.execute(
            "DROP TRIGGER advanced_edge_evidence_no_update"
        )

    queue = build_phase9_work_queue(storage)

    assert queue.research_bundle_ready is False
    assert queue.items
    first = queue.items[0]
    assert first.task_type == "STORAGE_INTEGRITY"
    assert first.scope == "PHASE9_STORAGE"
    assert first.shell_command == (
        "pio phase9-storage-integrity --require-verified"
    )


def test_work_queue_surfaces_stale_phase8_currentness(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)

    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE model_registry
            SET status = 'ROLLED_BACK'
            WHERE model_id = 'champion'
            """
        )

    queue = build_phase9_work_queue(storage)

    currentness = [
        item for item in queue.items
        if item.task_type == "PHASE8_CURRENTNESS_REQUIRED"
    ]
    assert len(currentness) == 1
    assert queue.phase8_promoted is False
    assert "phase8-promotion-audit" in currentness[0].shell_command
    assert not any(
        item.task_type == "PHASE8_PROMOTION_REQUIRED"
        for item in queue.items
    )


def test_work_queue_snapshot_is_sanitized_and_immutable(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(
        storage,
        "pool-a",
        "2026-09-23T10:00:00+00:00",
    )
    criteria = Phase9ResearchBundleCriteria(
        min_mint_risk_pools=1,
        min_wallet_flow_pools=1,
        min_static_hedge_pools=1,
    )
    secret_rpc = "https://rpc.example.invalid/?token=secret-value"
    queue = build_phase9_work_queue(
        storage,
        criteria=criteria,
        rpc_url=secret_rpc,
    )

    snapshot = persist_phase9_work_queue_snapshot(
        storage,
        queue=queue,
        criteria=criteria,
        created_at="2026-09-23T18:00:00+00:00",
    )

    assert snapshot.snapshot_id > 0
    assert len(snapshot.queue_sha256) == 64
    assert snapshot.task_count == len(queue.items)

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT criteria_json, state_json
            FROM phase9_work_queue_snapshots
            WHERE id = ?
            """,
            (snapshot.snapshot_id,),
        ).fetchone()
        assert row is not None
        persisted_text = str(row[0]) + str(row[1])
        assert secret_rpc not in persisted_text
        assert "shell_command" not in str(row[1])
        assert "research_sources_current" in str(row[1])
        try:
            conn.execute(
                """
                UPDATE phase9_work_queue_snapshots
                SET state_json = '{}'
                WHERE id = ?
                """,
                (snapshot.snapshot_id,),
            )
        except sqlite3.IntegrityError as exc:
            assert "immutable" in str(exc)
        else:
            raise AssertionError("expected immutable snapshot update refusal")


class DummyPolicyAudit:
    def __init__(
        self,
        *,
        current=False,
        exists=True,
        reasons=(),
        rollback_required=None,
        status=None,
    ):
        self.current = current
        self.exists = exists
        self.reasons = tuple(reasons)
        self.rollback_required = rollback_required
        self.status = status

    def to_record(self):
        return {
            "current": self.current,
            "exists": self.exists,
            "reasons": list(self.reasons),
            "rollback_required": self.rollback_required,
            "status": self.status,
        }


class DummyAuthorizationReport:
    def __init__(self, *, ready=False, reasons=()):
        self.authorization_ready = ready
        self.reasons = tuple(reasons)


class DummyShadowReport:
    def __init__(self, *, ready=False, reasons=()):
        self.shadow_ready = ready
        self.reasons = tuple(reasons)


class DummyControlledReport:
    def __init__(self, *, ready=False, reasons=()):
        self.controlled_validation_ready = ready
        self.reasons = tuple(reasons)


class DummyPrewire:
    def __init__(self, *, ready):
        self.ready = ready


def seed_current_phase9(storage):
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(storage, report=bundle)
    from meteora_learner.phase9_validation import evaluate_phase9_promotion

    report = evaluate_phase9_promotion(storage)
    assert report.promotion_ready is True
    persist_phase9_promotion(storage, report=report)


def test_work_queue_does_not_repromote_after_new_research(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_current_phase9(storage)

    with storage.connect() as conn:
        promoted_at_before = str(
            conn.execute(
                """
                SELECT promoted_at
                FROM phase_promotion_evidence
                WHERE phase_name = 'PHASE9'
                """
            ).fetchone()[0]
        )

    seed_mint_risk_lineage(storage, "pool-c")

    queue = build_phase9_work_queue(storage)

    assert queue.phase9_current is True
    assert any(
        item.task_type == "REFRESH_RESEARCH_BUNDLE"
        for item in queue.items
    )
    assert not any(
        item.task_type == "PERSIST_PHASE9_PROMOTION"
        for item in queue.items
    )

    with storage.connect() as conn:
        promoted_at_after = str(
            conn.execute(
                """
                SELECT promoted_at
                FROM phase_promotion_evidence
                WHERE phase_name = 'PHASE9'
                """
            ).fetchone()[0]
        )
    assert promoted_at_after == promoted_at_before


def test_work_queue_advances_to_shadow_after_current_phase9(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_current_phase9(storage)

    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyPolicyAudit(
            current=False,
            exists=False,
            reasons=("authorization evidence is missing",),
        ),
    )
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_policy_authorization",
        lambda storage: DummyAuthorizationReport(
            ready=False,
            reasons=("qualifying shadow runs 0 are below 3",),
        ),
    )
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_shadow",
        lambda storage, cycle_id: DummyShadowReport(ready=True),
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "PHASE9_SHADOW_VALIDATION"
    )
    assert queue.phase9_current is True
    assert queue.policy_authorization_current is False
    assert task.scope == "cycle"
    assert "phase9-shadow-validate --cycle-id cycle" in task.shell_command


def test_work_queue_uses_phase9_dataset_shadow_without_cycle(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_current_phase9(storage)

    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyPolicyAudit(
            current=False,
            exists=False,
            reasons=("authorization evidence is missing",),
        ),
    )
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_policy_authorization",
        lambda storage: DummyAuthorizationReport(
            ready=False,
            reasons=("qualifying shadow runs 0 are below 3",),
        ),
    )
    monkeypatch.setattr(
        work_queue_module,
        "_latest_retraining_dataset_cycle",
        lambda storage: None,
    )
    monkeypatch.setattr(
        work_queue_module,
        "_latest_phase9_bandit_dataset_id",
        lambda storage: 88,
    )
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_shadow",
        lambda storage, dataset_evidence_id=None, **kwargs: (
            DummyShadowReport(ready=(dataset_evidence_id == 88))
        ),
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "PHASE9_SHADOW_VALIDATION"
    )
    assert task.scope == "phase9-dataset:88"
    assert task.shell_command == (
        "pio phase9-shadow-validate --dataset-evidence-id 88 "
        "--persist --require-ready"
    )
    assert "Phase 9 bandit dataset" in task.reason


def test_work_queue_requires_fresh_shadow_source_after_dataset_used(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_current_phase9(storage)
    storage.save_advanced_edge_evidence(
        edge_type="PHASE9_POST_PROMOTION_SHADOW_V1",
        pool_address="__PHASE9_SHADOW__",
        as_of="2026-09-23T13:00:00+00:00",
        status="SHADOW_READY",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "cycle_id": "phase9-dataset:88",
        },
    )

    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyPolicyAudit(
            current=False,
            exists=False,
            reasons=("authorization evidence is missing",),
        ),
    )
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_policy_authorization",
        lambda storage: DummyAuthorizationReport(
            ready=False,
            reasons=("qualifying shadow runs 1 are below 3",),
        ),
    )
    monkeypatch.setattr(
        work_queue_module,
        "_latest_retraining_dataset_cycle",
        lambda storage: None,
    )
    monkeypatch.setattr(
        work_queue_module,
        "_latest_phase9_bandit_dataset_id",
        lambda storage: 88,
    )
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_shadow",
        lambda storage, dataset_evidence_id=None, **kwargs: (
            DummyShadowReport(ready=(dataset_evidence_id == 88))
        ),
    )

    queue = build_phase9_work_queue(storage)

    blocker = next(
        item for item in queue.items
        if item.task_type == "POST_PROMOTION_SHADOW_REQUIRED"
    )
    assert blocker.scope == "FRESH_CHECKSUM_BOUND_DATASET"
    assert blocker.shell_command is None
    assert "already represented" in blocker.reason
    assert not any(
        item.task_type == "PHASE9_SHADOW_VALIDATION"
        and item.scope == "phase9-dataset:88"
        for item in queue.items
    )


def test_work_queue_persists_ready_authorization_gate(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_current_phase9(storage)

    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyPolicyAudit(
            current=False,
            reasons=("persisted authorization is stale",),
        ),
    )
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_policy_authorization",
        lambda storage: DummyAuthorizationReport(ready=True),
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "PERSIST_POLICY_AUTHORIZATION"
    )
    assert task.shell_command == (
        "pio phase9-policy-authorization-gate "
        "--persist --require-ready"
    )


def test_work_queue_advances_to_fresh_controlled_holdout(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_current_phase9(storage)

    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_controlled_validation",
        lambda storage: DummyPolicyAudit(
            current=False,
            reasons=("controlled evidence is missing",),
        ),
    )
    monkeypatch.setattr(
        work_queue_module,
        "_latest_controlled_validation_cycle",
        lambda storage: "fresh-cycle",
    )
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_policy_controlled_validation",
        lambda storage, cycle_id: DummyControlledReport(ready=True),
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "PERSIST_CONTROLLED_VALIDATION"
    )
    assert queue.policy_authorization_current is True
    assert queue.controlled_validation_current is False
    assert task.scope == "fresh-cycle"
    assert "phase9-policy-controlled-validate" in task.shell_command


def test_work_queue_uses_phase9_dataset_for_controlled_holdout(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_current_phase9(storage)

    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_controlled_validation",
        lambda storage: DummyPolicyAudit(
            current=False,
            reasons=("controlled evidence is missing",),
        ),
    )
    monkeypatch.setattr(
        work_queue_module,
        "_latest_controlled_validation_cycle",
        lambda storage: None,
    )
    monkeypatch.setattr(
        work_queue_module,
        "_latest_retraining_dataset_cycle",
        lambda storage: None,
    )
    monkeypatch.setattr(
        work_queue_module,
        "_latest_phase9_bandit_dataset_id",
        lambda storage: 99,
    )
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_policy_controlled_validation",
        lambda storage, dataset_evidence_id=None, **kwargs: (
            DummyControlledReport(ready=(dataset_evidence_id == 99))
        ),
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "PERSIST_CONTROLLED_VALIDATION"
    )
    assert task.scope == "phase9-dataset:99"
    assert task.shell_command == (
        "pio phase9-policy-controlled-validate "
        "--dataset-evidence-id 99 --persist --require-ready"
    )
    assert "checksum-bound holdout" in task.reason


def test_work_queue_advances_to_bounded_rollout_after_policy_readiness(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_current_phase9(storage)

    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_controlled_validation",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_rollout_simulation",
        lambda storage: DummyPolicyAudit(
            current=False,
            reasons=("rollout simulation is missing",),
        ),
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "BOUNDED_ROLLOUT_SIMULATION"
    )
    assert queue.controlled_validation_current is True
    assert queue.rollout_simulation_current is False
    assert "<ROLLOUT_ENVELOPE_JSON>" in task.shell_command


def test_work_queue_surfaces_pending_rollback_observation_depth(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_current_phase9(storage)

    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_controlled_validation",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_rollout_simulation",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_rollback_simulation",
        lambda storage: DummyPolicyAudit(
            current=True,
            rollback_required=False,
            status="OBSERVATION_PENDING",
        ),
    )
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_policy_prewire_audit",
        lambda storage: DummyPrewire(ready=False),
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "ROLLBACK_OBSERVATION_DEPTH"
    )
    assert queue.rollout_simulation_current is True
    assert queue.rollback_simulation_current is True
    assert queue.prewire_ready is False
    assert "<UPDATED_ROLLBACK_METRICS_JSON>" in task.shell_command


def test_work_queue_reports_prewire_ready_when_chain_is_current(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_current_phase9(storage)

    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_controlled_validation",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_rollout_simulation",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_rollback_simulation",
        lambda storage: DummyPolicyAudit(
            current=True,
            rollback_required=False,
            status="NO_ROLLBACK_TRIGGER",
        ),
    )
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_policy_prewire_audit",
        lambda storage: DummyPrewire(ready=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_prewire_manifest",
        lambda storage: DummyPolicyAudit(current=True),
    )

    queue = build_phase9_work_queue(storage)

    policy_tasks = {
        "PHASE9_SHADOW_VALIDATION",
        "POST_PROMOTION_SHADOW_REQUIRED",
        "PERSIST_POLICY_AUTHORIZATION",
        "PERSIST_CONTROLLED_VALIDATION",
        "FRESH_CONTROLLED_HOLDOUT_REQUIRED",
        "BOUNDED_ROLLOUT_SIMULATION",
        "ROLLBACK_SIMULATION",
        "ROLLBACK_REMEDIATION_REQUIRED",
        "ROLLBACK_OBSERVATION_DEPTH",
        "PERSIST_PREWIRE_MANIFEST",
    }
    assert queue.phase9_current is True
    assert queue.policy_authorization_current is True
    assert queue.controlled_validation_current is True
    assert queue.rollout_simulation_current is True
    assert queue.rollback_simulation_current is True
    assert queue.prewire_ready is True
    assert queue.prewire_manifest_current is True
    assert not any(
        item.task_type in policy_tasks
        for item in queue.items
    )


def test_work_queue_persists_manifest_after_prewire_ready(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_current_phase9(storage)

    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_controlled_validation",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_rollout_simulation",
        lambda storage: DummyPolicyAudit(current=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_rollback_simulation",
        lambda storage: DummyPolicyAudit(
            current=True,
            rollback_required=False,
            status="NO_ROLLBACK_TRIGGER",
        ),
    )
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_policy_prewire_audit",
        lambda storage: DummyPrewire(ready=True),
    )
    monkeypatch.setattr(
        work_queue_module,
        "audit_persisted_phase9_policy_prewire_manifest",
        lambda storage: DummyPolicyAudit(
            current=False,
            reasons=("pre-wiring manifest is missing",),
        ),
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "PERSIST_PREWIRE_MANIFEST"
    )
    assert queue.prewire_ready is True
    assert queue.prewire_manifest_current is False
    assert task.shell_command == (
        "pio phase9-policy-manifest --persist --require-ready"
    )


def test_work_queue_historical_cutoff_refuses_later_chain_backfill(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_pool(
            storage,
            pool,
            "2026-09-23T14:00:00+00:00",
        )

    queue = build_phase9_work_queue(
        storage,
        as_of="2026-09-23T13:00:00+00:00",
    )

    assert queue.candidate_pools == ()
    gap = next(
        item for item in queue.items
        if item.task_type == "HISTORICAL_CHAIN_POOL_GAP"
    )
    assert gap.shell_command is None
    assert "cannot backfill" in gap.reason
    assert not any(
        item.task_type in {"CHAIN_POOL_CAPTURE_PLAN", "API_POOL_DISCOVERY"}
        for item in queue.items
    )


def test_work_queue_historical_history_deficit_has_no_capture_command(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        for index in range(10):
            save_pool(
                storage,
                pool,
                f"2026-09-23T12:00:{index:02d}+00:00",
            )
        for index in range(40):
            save_pool(
                storage,
                pool,
                f"2026-09-23T14:00:{index:02d}+00:00",
            )

    queue = build_phase9_work_queue(
        storage,
        as_of="2026-09-23T13:00:00+00:00",
    )

    history = next(
        item for item in queue.items
        if item.task_type == "CHAIN_HISTORY_DEPTH"
    )
    assert history.shell_command is None
    assert "cannot be backfilled" in history.reason
    assert "pool-a:33" in history.reason
    adaptive = next(
        item for item in queue.items
        if item.task_type == "ADAPTIVE_MULTI_POOL"
    )
    assert adaptive.shell_command is None


def test_work_queue_surfaces_chain_capture_plan_from_api_discovery(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        work_queue_module,
        "utc_now_iso",
        lambda: "2026-09-23T13:00:00+00:00",
    )
    with storage.connect() as conn:
        for rank, pool in enumerate(("pool-a", "pool-b", "pool-c"), start=1):
            conn.execute(
                """
                INSERT INTO pool_snapshots(
                    observed_at, address, name, tvl,
                    volume_24h, fees_24h, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, '{}')
                """,
                (
                    "2026-09-23T12:00:00+00:00",
                    pool,
                    pool,
                    1_000.0 - rank * 100.0,
                    100.0,
                    1.0,
                ),
            )

    queue = build_phase9_work_queue(
        storage,
        rpc_url="https://rpc.example.invalid",
    )

    task = next(
        item for item in queue.items
        if item.task_type == "CHAIN_POOL_CAPTURE_PLAN"
    )
    assert task.scope == "PHASE9_CHAIN_POOLS"
    assert task.shell_command is not None
    assert "phase9-chain-capture-plan" in task.shell_command
    assert "https://rpc.example.invalid" in task.shell_command


def test_work_queue_requests_fresh_api_ranking_when_only_stale_rows_exist(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        work_queue_module,
        "utc_now_iso",
        lambda: "2026-09-23T20:00:00+00:00",
    )
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_pool(
            storage,
            pool,
            "2026-09-23T19:00:00+00:00",
        )
    with storage.connect() as conn:
        for rank, pool in enumerate(
            ("pool-a", "pool-b", "pool-c"),
            start=1,
        ):
            conn.execute(
                """
                INSERT INTO pool_snapshots(
                    observed_at, address, name, tvl,
                    volume_24h, fees_24h, raw_json
                ) VALUES (
                    '2026-09-23T12:00:00+00:00',
                    ?, ?, ?, 100, 1, '{}'
                )
                """,
                (pool, pool, 1_000 - rank * 100),
            )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "API_RANKING_REFRESH"
    )
    assert task.scope == "METEORA_POOLS"
    assert task.shell_command == "pio collect-once"
    assert "0/3" in task.reason
    assert "stale ranking snapshot" in task.reason
    assert not any(
        item.task_type == "API_POOL_DISCOVERY"
        for item in queue.items
    )


def test_work_queue_requests_api_pool_discovery_when_no_candidates_exist(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "API_POOL_DISCOVERY"
    )
    assert task.scope == "METEORA_POOLS"
    assert task.shell_command == "pio collect-once"


def test_work_queue_surfaces_replay_valid_source_refresh(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)

    values = {
        "adaptive_regime": False,
        "mint_risk": True,
        "wallet_flow": True,
        "portfolio_allocation": True,
        "static_hedge": True,
        "contextual_bandit": True,
    }
    monkeypatch.setattr(
        work_queue_module,
        "evaluate_phase9_source_freshness",
        lambda *args, **kwargs: SimpleNamespace(
            families=tuple(
                SimpleNamespace(
                    family=family,
                    current=current,
                    reason=(
                        "new chain snapshot available"
                        if not current
                        else "current"
                    ),
                )
                for family, current in values.items()
            ),
            by_family=lambda: dict(values),
        ),
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "RESEARCH_SOURCE_REFRESH"
    )
    assert task.scope == "adaptive_regime"
    assert task.shell_command == "pio phase9-research-refresh-run"
    assert "newer source evidence is available" in task.reason
    assert "new chain snapshot available" in task.reason
    assert queue.research_sources_current is False
