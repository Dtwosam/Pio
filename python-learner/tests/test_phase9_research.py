from meteora_learner.adaptive_range import AdaptiveRangeCriteria
from meteora_learner.adaptive_range_validation import (
    AdaptiveRangeValidationCriteria,
)
from meteora_learner.market_regime import DLMMRegimeCriteria
from meteora_learner.phase9_research import (
    PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
    Phase9ResearchCriteria,
    evaluate_phase9_research,
    persist_phase9_research,
)
from meteora_learner.phase_promotion import (
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
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
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


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
