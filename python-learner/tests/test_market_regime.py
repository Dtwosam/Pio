from meteora_learner.market_regime import (
    DLMMRegimeCriteria,
    REGIME_QUIET,
    REGIME_TREND_UP,
    REGIME_VOLATILE_CHOP,
    classify_dlmm_regime,
)
from meteora_learner.phase_promotion import (
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
)
from meteora_learner.storage import Storage


def add_snapshot(storage, index, active_bin_id, pool="pool"):
    storage_time = f"2026-09-23T{index:02d}:00:00+00:00"
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(
                observed_at, pool_address, active_bin_id, bin_step,
                token_x_mint, token_y_mint, raw_json
            ) VALUES (?, ?, ?, 25, 'x', 'y', '{}')
            """,
            (storage_time, pool, active_bin_id),
        )


def criteria():
    return DLMMRegimeCriteria(
        lookback_observations=24,
        recent_observations=6,
        min_observations=12,
        trend_efficiency_threshold=0.65,
        activity_percentile=0.75,
        quiet_percentile=0.25,
    )


def promote_phase8(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def test_trending_path_is_detected_without_live_authority(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    values = [
        100, 100, 101, 100, 101, 101, 102, 101,
        102, 102, 103, 102, 103, 104, 105, 106,
        107, 108, 109, 110,
    ]
    for index, value in enumerate(values):
        add_snapshot(storage, index, value)
    promote_phase8(storage)

    report = classify_dlmm_regime(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T19:00:00+00:00",
    )

    assert report.status == "RESEARCH_READY"
    assert report.regime == REGIME_TREND_UP
    assert report.recent_directional_efficiency == 1.0
    assert report.policy_actionable is False
    assert len(report.source_snapshot_ids) == 20
    assert len(report.source_snapshot_sha256) == 64


def test_volatile_chop_is_detected(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    values = [
        100, 101, 100, 101, 100, 101, 100, 101,
        100, 101, 100, 101, 100, 105, 100, 105,
        100, 105, 100, 105,
    ]
    for index, value in enumerate(values):
        add_snapshot(storage, index, value)
    promote_phase8(storage)

    report = classify_dlmm_regime(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T19:00:00+00:00",
    )

    assert report.regime == REGIME_VOLATILE_CHOP
    assert report.recent_directional_efficiency < 0.65
    assert report.policy_actionable is False


def test_quiet_recent_path_is_detected(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    values = [
        100, 101, 100, 101, 100, 101, 100, 101,
        100, 101, 100, 101, 100, 100, 100, 100,
        100, 100, 100, 100,
    ]
    for index, value in enumerate(values):
        add_snapshot(storage, index, value)

    report = classify_dlmm_regime(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T19:00:00+00:00",
    )

    assert report.regime == REGIME_QUIET
    assert report.status == "RESEARCH_ONLY_PHASE8_BLOCKED"
    assert report.recent_path_bins == 0


def test_regime_cutoff_excludes_future_extremes(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    values = [
        100, 100, 101, 100, 101, 101, 102, 101,
        102, 102, 103, 102, 103, 104, 105, 106,
        107, 108, 109, 110,
    ]
    for index, value in enumerate(values):
        add_snapshot(storage, index, value)

    before = classify_dlmm_regime(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T19:00:00+00:00",
    )

    add_snapshot(storage, 20, 200)
    add_snapshot(storage, 21, 20)

    after = classify_dlmm_regime(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T19:00:00+00:00",
    )

    assert after.regime == before.regime
    assert after.latest_active_bin_id == before.latest_active_bin_id
    assert (
        after.historical_active_step_threshold
        == before.historical_active_step_threshold
    )
