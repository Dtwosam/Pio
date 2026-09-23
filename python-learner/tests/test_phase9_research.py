from meteora_learner.adaptive_range import AdaptiveRangeCriteria
from meteora_learner.adaptive_range_validation import (
    AdaptiveRangeValidationCriteria,
)
from meteora_learner.market_regime import DLMMRegimeCriteria
from meteora_learner.phase8_validation import (
    Phase8PromotionCriteria,
    evaluate_phase8_promotion,
)
from meteora_learner.phase9_research import (
    PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
    Phase9ResearchCriteria,
    evaluate_phase9_research,
    persist_phase9_research,
)
from meteora_learner.phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
    persist_phase8_promotion,
)
from meteora_learner.storage import Storage


POOLS = ("pool-a", "pool-b", "pool-c")


def add_snapshot(storage, pool, index, active_bin_id):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(
                observed_at, pool_address, active_bin_id, bin_step,
                token_x_mint, token_y_mint, raw_json
            ) VALUES (?, ?, ?, 25, 'x', 'y', '{}')
            """,
            (
                f"2026-09-23T{index:02d}:00:00+00:00",
                pool,
                active_bin_id,
            ),
        )


def seed_history(storage, pool, offset=0):
    values = [
        100, 102, 104, 102, 100, 102, 104, 102,
        100, 102, 104, 102, 100, 102, 104, 102,
        100, 102, 104, 102, 100, 102, 104, 102,
    ]
    for index, value in enumerate(values):
        add_snapshot(storage, pool, index, value + offset)


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


def adaptive_criteria():
    return AdaptiveRangeCriteria(
        lookback_observations=20,
        holding_observations=2,
        target_coverage=0.80,
        min_half_width_bins=1,
        max_half_width_bins=6,
        min_historical_windows=4,
    )


def adaptive_validation_criteria():
    return AdaptiveRangeValidationCriteria(
        fixed_half_width_bins=1,
        min_decisions=8,
        min_adaptive_survival_rate=0.75,
        min_survival_uplift_vs_fixed=0.25,
        max_mean_width_multiple_vs_fixed=5.0,
        max_cap_exceeded_rate=0.0,
    )


def regime_criteria():
    return DLMMRegimeCriteria(
        lookback_observations=24,
        recent_observations=6,
        min_observations=12,
        trend_efficiency_threshold=0.65,
        activity_percentile=0.75,
        quiet_percentile=0.25,
    )


def phase9_criteria():
    return Phase9ResearchCriteria(
        min_pools=3,
        min_qualified_pools=2,
        min_qualified_pool_rate=0.67,
        min_mean_survival_uplift_vs_fixed=0.25,
        max_mean_width_multiple_vs_fixed=5.0,
    )


def seed_all(storage):
    for index, pool in enumerate(POOLS):
        seed_history(storage, pool, offset=index * 10)


def test_multi_pool_phase9_research_can_qualify(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_all(storage)
    promote_phase8(storage)

    report = evaluate_phase9_research(
        storage,
        pool_addresses=POOLS,
        criteria=phase9_criteria(),
        adaptive_criteria=adaptive_criteria(),
        adaptive_validation_criteria=adaptive_validation_criteria(),
        regime_criteria=regime_criteria(),
        as_of="2026-09-23T23:00:00+00:00",
    )

    assert report.status == "QUALIFIED_RESEARCH"
    assert report.research_qualified is True
    assert report.pools_seen == 3
    assert report.pools_qualified == 3
    assert report.qualified_pool_rate == 1.0
    assert report.mean_survival_uplift_vs_fixed >= 0.25
    assert report.policy_actionable is False
    assert all(item.research_qualified for item in report.pools)


def test_phase8_dependency_blocks_phase9_qualification(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_all(storage)

    report = evaluate_phase9_research(
        storage,
        pool_addresses=POOLS,
        criteria=phase9_criteria(),
        adaptive_criteria=adaptive_criteria(),
        adaptive_validation_criteria=adaptive_validation_criteria(),
        regime_criteria=regime_criteria(),
        as_of="2026-09-23T23:00:00+00:00",
    )

    assert report.status == "RESEARCH_ONLY_PHASE8_BLOCKED"
    assert report.research_qualified is False
    assert any("Phase 8" in reason for reason in report.reasons)
    assert report.policy_actionable is False


def test_multi_pool_research_evidence_round_trip(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_all(storage)
    promote_phase8(storage)

    report = evaluate_phase9_research(
        storage,
        pool_addresses=POOLS,
        criteria=phase9_criteria(),
        adaptive_criteria=adaptive_criteria(),
        adaptive_validation_criteria=adaptive_validation_criteria(),
        regime_criteria=regime_criteria(),
        as_of="2026-09-23T23:00:00+00:00",
    )
    evidence_id = persist_phase9_research(
        storage,
        report=report,
    )

    assert evidence_id > 0
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
    )
    assert latest is not None
    assert latest["qualified"] is True
    assert latest["status"] == "QUALIFIED_RESEARCH"
    assert latest["evidence"]["pools_seen"] == 3
    assert latest["evidence"]["policy_actionable"] is False


def test_duplicate_pool_inputs_do_not_inflate_coverage(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_history(storage, "pool-a")
    promote_phase8(storage)

    report = evaluate_phase9_research(
        storage,
        pool_addresses=["pool-a", "pool-a", "pool-a"],
        criteria=phase9_criteria(),
        adaptive_criteria=adaptive_criteria(),
        adaptive_validation_criteria=adaptive_validation_criteria(),
        regime_criteria=regime_criteria(),
        as_of="2026-09-23T23:00:00+00:00",
    )

    assert report.pools_seen == 1
    assert report.research_qualified is False
    assert any("pools seen" in reason for reason in report.reasons)
