from datetime import datetime, timedelta, timezone

from meteora_learner.continuous_promotion import CONTINUOUS_PROMOTION_EVIDENCE_TYPE
from meteora_learner.phase8_validation import (
    Phase8PromotionCriteria,
    evaluate_phase8_promotion,
)
from meteora_learner.liquidity_math import Q64
from meteora_learner.phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
    persist_phase8_promotion,
)
from meteora_learner.static_hedge import (
    STATIC_HEDGE_EVIDENCE_TYPE,
    HedgeInstrumentAssumptions,
    StaticHedgeCriteria,
    persist_static_hedge_research,
    research_static_inventory_hedge,
)
from meteora_learner.storage import Storage


def q64_ratio(numerator, denominator=100):
    return Q64 * numerator // denominator


def add_observation(storage, index, price_q64, pool="pool"):
    observed_at = (
        datetime(2026, 9, 23, tzinfo=timezone.utc)
        + timedelta(hours=index)
    ).isoformat()
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(
                observed_at, pool_address, active_bin_id, bin_step,
                token_x_mint, token_y_mint, raw_json
            ) VALUES (?, ?, 0, 25, 'x', 'y', '{}')
            """,
            (observed_at, pool),
        )
        conn.execute(
            """
            INSERT INTO bin_liquidity_snapshots(
                observed_at, pool_address, bin_array_index,
                bin_id, price, amount_x, amount_y,
                liquidity_supply,
                fee_amount_x_per_token_stored,
                fee_amount_y_per_token_stored
            ) VALUES (?, ?, 0, 0, ?, '1000', '1000',
                      '1000', '0', '0')
            """,
            (observed_at, pool, str(price_q64)),
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
def seed_oscillating_path(storage):
    prices = [
        100, 110, 90, 115, 85, 112, 88, 118,
        82, 110, 90, 116, 84, 108, 92, 114,
        86, 109, 91, 111, 89, 113, 87, 110,
    ]
    for index, price in enumerate(prices):
        add_observation(storage, index, q64_ratio(price))


def instrument(**overrides):
    values = {
        "instrument_id": "SOL-PERP",
        "venue": "RESEARCH_VENUE",
        "available_liquidity_y_atomic": 1_000_000.0,
        "max_liquidity_share_bps": 1_000,
        "max_leverage": 1.0,
        "funding_bps_per_holding_window": 0.0,
    }
    values.update(overrides)
    return HedgeInstrumentAssumptions(**values)


def criteria(**overrides):
    values = {
        "observation_limit": 24,
        "holding_observations": 2,
        "hedge_fraction": 1.0,
        "hedge_round_trip_cost_bps": 0.0,
        "min_windows": 10,
        "min_mean_abs_return_reduction_bps": 100.0,
        "min_worst_loss_improvement_bps": 100.0,
        "max_mean_return_drag_bps": 1000.0,
    }
    values.update(overrides)
    return StaticHedgeCriteria(**values)


def test_full_static_x_hedge_reduces_directional_variability(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_oscillating_path(storage)
    promote_phase8(storage)

    report = research_static_inventory_hedge(
        storage,
        pool_address="pool",
        amount_x=1_000,
        amount_y=0,
        instrument=instrument(),
        criteria=criteria(),
        as_of="2026-09-23T23:00:00+00:00",
    )

    assert report.status == "QUALIFIED_RESEARCH"
    assert report.research_qualified is True
    assert report.windows >= 10
    assert report.mean_abs_unhedged_return_bps > 0
    assert abs(report.mean_abs_hedged_return_bps) < 1e-9
    assert report.mean_abs_return_reduction_bps > 100
    assert report.worst_loss_improvement_bps > 100
    assert report.policy_actionable is False
    assert len(report.source_observations) == 24
    assert len(report.source_path_sha256) == 64
    assert report.source_observations[0].pool_snapshot_id > 0
    assert report.source_observations[0].bin_liquidity_snapshot_id > 0


def test_hedge_cost_can_disqualify_research(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_oscillating_path(storage)
    promote_phase8(storage)

    report = research_static_inventory_hedge(
        storage,
        pool_address="pool",
        amount_x=1_000,
        amount_y=0,
        instrument=instrument(),
        criteria=criteria(
            hedge_round_trip_cost_bps=500.0,
            max_mean_return_drag_bps=100.0,
        ),
        as_of="2026-09-23T23:00:00+00:00",
    )

    assert report.research_qualified is False
    assert report.status == "NOT_QUALIFIED"
    assert report.mean_return_drag_bps > 100
    assert any(
        "mean return drag" in reason
        for reason in report.reasons
    )


def test_hedge_research_is_no_lookahead(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_oscillating_path(storage)
    cutoff = "2026-09-23T20:00:00+00:00"

    before = research_static_inventory_hedge(
        storage,
        pool_address="pool",
        amount_x=1_000,
        amount_y=0,
        instrument=instrument(),
        criteria=criteria(),
        as_of=cutoff,
    )

    add_observation(storage, 24, q64_ratio(300))
    add_observation(storage, 25, q64_ratio(20))

    after = research_static_inventory_hedge(
        storage,
        pool_address="pool",
        amount_x=1_000,
        amount_y=0,
        instrument=instrument(),
        criteria=criteria(),
        as_of=cutoff,
    )

    assert after.windows == before.windows
    assert after.window_results == before.window_results
    assert after.source_observations == before.source_observations
    assert after.source_path_sha256 == before.source_path_sha256
    assert (
        after.mean_abs_return_reduction_bps
        == before.mean_abs_return_reduction_bps
    )


def test_phase8_dependency_blocks_hedge_qualification(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_oscillating_path(storage)

    report = research_static_inventory_hedge(
        storage,
        pool_address="pool",
        amount_x=1_000,
        amount_y=0,
        instrument=instrument(),
        criteria=criteria(),
        as_of="2026-09-23T23:00:00+00:00",
    )

    assert report.status == "RESEARCH_ONLY_PHASE8_BLOCKED"
    assert report.research_qualified is False
    assert report.policy_actionable is False


def test_hedge_evidence_round_trip(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_oscillating_path(storage)
    promote_phase8(storage)
    report = research_static_inventory_hedge(
        storage,
        pool_address="pool",
        amount_x=1_000,
        amount_y=0,
        instrument=instrument(),
        criteria=criteria(),
        as_of="2026-09-23T23:00:00+00:00",
    )

    evidence_id = persist_static_hedge_research(
        storage,
        report=report,
    )

    assert evidence_id > 0
    latest = storage.latest_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool",
    )
    assert latest is not None
    assert latest["qualified"] is True
    assert latest["evidence"]["research_only"] is True
    assert latest["evidence"]["policy_actionable"] is False


def test_liquidity_assumption_can_disqualify_hedge_research(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_oscillating_path(storage)
    promote_phase8(storage)

    report = research_static_inventory_hedge(
        storage,
        pool_address="pool",
        amount_x=1_000,
        amount_y=0,
        instrument=instrument(
            available_liquidity_y_atomic=500.0,
            max_liquidity_share_bps=100,
        ),
        criteria=criteria(),
        as_of="2026-09-23T23:00:00+00:00",
    )

    assert report.research_qualified is False
    assert report.liquidity_constrained_windows > 0
    assert any(
        "liquidity-share cap" in reason
        for reason in report.reasons
    )
