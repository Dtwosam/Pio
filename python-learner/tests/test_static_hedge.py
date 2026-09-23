from meteora_learner.liquidity_math import Q64
from meteora_learner.phase_promotion import (
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
)
from meteora_learner.static_hedge import (
    STATIC_HEDGE_EVIDENCE_TYPE,
    StaticHedgeCriteria,
    persist_static_hedge_research,
    research_static_inventory_hedge,
)
from meteora_learner.storage import Storage


def q64_ratio(numerator, denominator=100):
    return Q64 * numerator // denominator


def add_observation(storage, index, price_q64, pool="pool"):
    observed_at = f"2026-09-23T{index:02d}:00:00+00:00"
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
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def seed_oscillating_path(storage):
    prices = [
        100, 110, 90, 115, 85, 112, 88, 118,
        82, 110, 90, 116, 84, 108, 92, 114,
        86, 109, 91, 111, 89, 113, 87, 110,
    ]
    for index, price in enumerate(prices):
        add_observation(storage, index, q64_ratio(price))


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


def test_hedge_cost_can_disqualify_research(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_oscillating_path(storage)
    promote_phase8(storage)

    report = research_static_inventory_hedge(
        storage,
        pool_address="pool",
        amount_x=1_000,
        amount_y=0,
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
        criteria=criteria(),
        as_of=cutoff,
    )

    assert after.windows == before.windows
    assert after.window_results == before.window_results
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
