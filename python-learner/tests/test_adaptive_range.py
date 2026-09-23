from meteora_learner.adaptive_range import (
    AdaptiveRangeCriteria,
    research_adaptive_range,
)
from meteora_learner.phase_promotion import (
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
)
from meteora_learner.storage import Storage


def add_snapshot(storage, observed_at, active_bin_id, pool="pool"):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(
                observed_at, pool_address, active_bin_id, bin_step,
                token_x_mint, token_y_mint, raw_json
            ) VALUES (?, ?, ?, 25, 'x', 'y', '{}')
            """,
            (observed_at, pool, active_bin_id),
        )


def criteria(**overrides):
    values = {
        "lookback_observations": 20,
        "holding_observations": 2,
        "target_coverage": 0.80,
        "min_half_width_bins": 1,
        "max_half_width_bins": 20,
        "min_historical_windows": 3,
    }
    values.update(overrides)
    return AdaptiveRangeCriteria(**values)


def seed_history(storage):
    for index, active_bin_id in enumerate(
        [100, 101, 102, 102, 103, 104, 103, 105]
    ):
        add_snapshot(
            storage,
            f"2026-09-23T{index:02d}:00:00+00:00",
            active_bin_id,
        )


def test_adaptive_range_is_no_lookahead(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_history(storage)

    before = research_adaptive_range(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T07:00:00+00:00",
    )

    add_snapshot(
        storage,
        "2026-09-23T08:00:00+00:00",
        160,
    )
    add_snapshot(
        storage,
        "2026-09-23T09:00:00+00:00",
        80,
    )

    after = research_adaptive_range(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T07:00:00+00:00",
    )

    assert after.observations_used == before.observations_used
    assert (
        after.recommended_half_width_bins
        == before.recommended_half_width_bins
    )
    assert after.latest_active_bin_id == 105
    assert after.status == "RESEARCH_ONLY_PHASE8_BLOCKED"
    assert after.policy_actionable is False


def test_phase8_promotion_allows_research_ready_but_not_live_authority(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_history(storage)
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )

    report = research_adaptive_range(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T07:00:00+00:00",
    )

    assert report.phase8_promoted is True
    assert report.status == "RESEARCH_READY"
    assert report.recommended_half_width_bins is not None
    assert report.empirical_coverage is not None
    assert report.research_only is True
    assert report.policy_actionable is False


def test_range_cap_exceeded_fails_closed(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for index, active_bin_id in enumerate(
        [100, 120, 80, 130, 70, 140, 60, 150]
    ):
        add_snapshot(
            storage,
            f"2026-09-23T{index:02d}:00:00+00:00",
            active_bin_id,
        )
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )

    report = research_adaptive_range(
        storage,
        pool_address="pool",
        criteria=criteria(max_half_width_bins=10),
        as_of="2026-09-23T07:00:00+00:00",
    )

    assert report.status == "RANGE_CAP_EXCEEDED"
    assert report.raw_recommended_half_width_bins > 10
    assert report.recommended_half_width_bins == 10
    assert report.policy_actionable is False


def test_insufficient_historical_windows_is_explicit(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    add_snapshot(storage, "2026-09-23T00:00:00+00:00", 100)
    add_snapshot(storage, "2026-09-23T01:00:00+00:00", 101)
    add_snapshot(storage, "2026-09-23T02:00:00+00:00", 102)

    report = research_adaptive_range(
        storage,
        pool_address="pool",
        criteria=criteria(
            holding_observations=2,
            min_historical_windows=3,
        ),
        as_of="2026-09-23T02:00:00+00:00",
    )

    assert report.status == "INSUFFICIENT_WINDOWS"
    assert report.historical_windows == 1
    assert report.recommended_half_width_bins is None
