from meteora_learner.adaptive_range import AdaptiveRangeCriteria
from meteora_learner.adaptive_range_validation import (
    AdaptiveRangeValidationCriteria,
    validate_adaptive_range_walk_forward,
)
from meteora_learner.phase_promotion import (
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
)
from meteora_learner.storage import Storage


def add_snapshot(storage, index, active_bin_id, pool="pool"):
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


def adaptive_criteria():
    return AdaptiveRangeCriteria(
        lookback_observations=20,
        holding_observations=2,
        target_coverage=0.80,
        min_half_width_bins=1,
        max_half_width_bins=6,
        min_historical_windows=4,
    )


def validation_criteria():
    return AdaptiveRangeValidationCriteria(
        fixed_half_width_bins=1,
        min_decisions=8,
        min_adaptive_survival_rate=0.75,
        min_survival_uplift_vs_fixed=0.25,
        max_mean_width_multiple_vs_fixed=5.0,
        max_cap_exceeded_rate=0.0,
    )


def promote_phase8(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def seed_moving_history(storage):
    values = [
        100, 102, 104, 102, 100, 102, 104, 102,
        100, 102, 104, 102, 100, 102, 104, 102,
        100, 102, 104, 102, 100, 102, 104, 102,
    ]
    for index, value in enumerate(values):
        add_snapshot(storage, index, value)


def test_walk_forward_adaptive_range_can_beat_fixed_width(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_moving_history(storage)
    promote_phase8(storage)

    report = validate_adaptive_range_walk_forward(
        storage,
        pool_address="pool",
        adaptive_criteria=adaptive_criteria(),
        validation_criteria=validation_criteria(),
        as_of="2026-09-23T23:00:00+00:00",
    )

    assert report.status == "QUALIFIED_RESEARCH"
    assert report.research_qualified is True
    assert report.decisions >= 8
    assert report.adaptive_survival_rate is not None
    assert report.fixed_survival_rate is not None
    assert (
        report.adaptive_survival_rate
        > report.fixed_survival_rate
    )
    assert report.survival_uplift_vs_fixed >= 0.25
    assert report.mean_width_multiple_vs_fixed <= 5.0
    assert report.policy_actionable is False


def test_walk_forward_is_unchanged_by_future_snapshots(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_moving_history(storage)
    cutoff = "2026-09-23T20:00:00+00:00"

    before = validate_adaptive_range_walk_forward(
        storage,
        pool_address="pool",
        adaptive_criteria=adaptive_criteria(),
        validation_criteria=validation_criteria(),
        as_of=cutoff,
    )

    add_snapshot(storage, 24, 180)
    add_snapshot(storage, 25, 20)

    after = validate_adaptive_range_walk_forward(
        storage,
        pool_address="pool",
        adaptive_criteria=adaptive_criteria(),
        validation_criteria=validation_criteria(),
        as_of=cutoff,
    )

    assert after.decisions == before.decisions
    assert (
        after.adaptive_survival_rate
        == before.adaptive_survival_rate
    )
    assert after.fixed_survival_rate == before.fixed_survival_rate
    assert (
        after.mean_adaptive_half_width_bins
        == before.mean_adaptive_half_width_bins
    )
    assert after.decision_results == before.decision_results


def test_qualification_does_not_create_live_authority(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_moving_history(storage)

    report = validate_adaptive_range_walk_forward(
        storage,
        pool_address="pool",
        adaptive_criteria=adaptive_criteria(),
        validation_criteria=validation_criteria(),
        as_of="2026-09-23T23:00:00+00:00",
    )

    assert report.research_qualified is True
    assert report.status == "RESEARCH_ONLY_PHASE8_BLOCKED"
    assert report.research_only is True
    assert report.policy_actionable is False


def test_width_cost_can_disqualify_trivial_widening(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_moving_history(storage)
    promote_phase8(storage)

    strict_width = AdaptiveRangeValidationCriteria(
        fixed_half_width_bins=1,
        min_decisions=8,
        min_adaptive_survival_rate=0.75,
        min_survival_uplift_vs_fixed=0.25,
        max_mean_width_multiple_vs_fixed=1.5,
        max_cap_exceeded_rate=0.0,
    )
    report = validate_adaptive_range_walk_forward(
        storage,
        pool_address="pool",
        adaptive_criteria=adaptive_criteria(),
        validation_criteria=strict_width,
        as_of="2026-09-23T23:00:00+00:00",
    )

    assert report.research_qualified is False
    assert report.status == "NOT_QUALIFIED"
    assert any(
        "mean width" in reason
        for reason in report.reasons
    )
