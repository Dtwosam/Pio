from meteora_learner.phase9_history_capture import (
    run_phase9_history_capture,
)
from meteora_learner.storage import Storage


def save_chain_observations(storage, pool, count):
    for index in range(count):
        storage.save_chain_pool_snapshot(
            {
                "pool_address": pool,
                "active_bin_id": index % 4,
                "bin_step": 25,
                "token_x_mint": f"{pool}-x",
                "token_y_mint": f"{pool}-y",
                "bin_arrays": [],
            },
            observed_at=(
                f"2026-09-23T12:00:{index:02d}+00:00"
            ),
        )


def payload(pool):
    return {
        "pool_address": pool,
        "active_bin_id": 100,
        "bin_step": 25,
        "token_x_mint": f"{pool}-x",
        "token_y_mint": f"{pool}-y",
        "token_x_program": (
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
        ),
        "token_y_program": (
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
        ),
        "bin_arrays": [
            {
                "address": f"{pool}-array",
                "index": 1,
                "lower_bin_id": 70,
                "upper_bin_id": 139,
                "bins": [
                    {
                        "bin_id": 100,
                        "price": "18446744073709551616",
                        "amount_x": "10",
                        "amount_y": "20",
                        "liquidity_supply": "30",
                        "fee_amount_x_per_token_stored": "40",
                        "fee_amount_y_per_token_stored": "50",
                    }
                ],
            }
        ],
    }


def test_history_capture_moves_three_42_observation_pools_to_ready(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_chain_observations(storage, pool, 42)

    calls = []

    def inspector(pool, radius):
        calls.append((pool, radius))
        return payload(pool)

    report = run_phase9_history_capture(
        storage,
        inspector=inspector,
        ingest_observed_at="2026-09-23T13:00:00+00:00",
    )

    assert report.history_ready_before is False
    assert report.history_ready_after is True
    assert report.observations_remaining_after == 0
    assert report.pools_attempted == 3
    assert report.pools_captured == 3
    assert report.pools_failed == 0
    assert all(
        item.capture_observed_at == "2026-09-23T13:00:00+00:00"
        for item in report.items
    )
    assert calls == [
        ("pool-a", 1),
        ("pool-b", 1),
        ("pool-c", 1),
    ]
    assert report.research_only is True
    assert report.read_only_capture is True
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_history_capture_rejects_non_increasing_explicit_timestamp(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_chain_observations(storage, pool, 42)

    calls = []

    report = run_phase9_history_capture(
        storage,
        inspector=lambda pool, radius: calls.append((pool, radius)),
        ingest_observed_at="2026-09-23T12:00:41+00:00",
    )

    assert report.history_ready_after is False
    assert report.pools_captured == 0
    assert report.pools_failed == 3
    assert calls == []
    assert all(
        "must be newer" in item.error
        for item in report.items
    )


def test_history_capture_skips_samples_inside_minimum_interval(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_chain_observations(storage, pool, 42)

    calls = []
    report = run_phase9_history_capture(
        storage,
        inspector=lambda pool, radius: calls.append((pool, radius)),
        ingest_observed_at="2026-09-23T12:30:00+00:00",
        min_observation_interval_seconds=3600,
    )

    assert calls == []
    assert report.pools_attempted == 3
    assert report.pools_captured == 0
    assert report.pools_skipped_interval == 3
    assert report.pools_failed == 0
    assert report.min_observation_interval_seconds == 3600
    assert report.history_ready_after is False
    assert report.observations_remaining_after == 3
    assert all(
        item.status == "SKIPPED_INTERVAL"
        for item in report.items
    )
    assert all(
        "minimum is 3600s" in item.error
        for item in report.items
    )
    assert any(
        "minimum observation interval has not elapsed" in reason
        for reason in report.reasons
    )


def test_history_capture_accepts_sample_at_exact_minimum_interval(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_chain_observations(storage, pool, 42)

    calls = []
    report = run_phase9_history_capture(
        storage,
        inspector=lambda pool, radius: (
            calls.append((pool, radius)) or payload(pool)
        ),
        ingest_observed_at="2026-09-23T13:00:41+00:00",
        min_observation_interval_seconds=3600,
    )

    assert report.pools_captured == 3
    assert report.pools_skipped_interval == 0
    assert report.pools_failed == 0
    assert report.history_ready_after is True
    assert len(calls) == 3


def test_history_capture_isolates_one_pool_failure(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_chain_observations(storage, pool, 42)

    def inspector(pool, radius):
        if pool == "pool-b":
            raise RuntimeError("rpc unavailable")
        return payload(pool)

    report = run_phase9_history_capture(
        storage,
        inspector=inspector,
        ingest_observed_at="2026-09-23T13:00:00+00:00",
    )

    assert report.history_ready_after is False
    assert report.pools_captured == 2
    assert report.pools_failed == 1
    assert report.observations_remaining_after == 1
    failed = next(
        item for item in report.items
        if item.pool_address == "pool-b"
    )
    assert failed.status == "FAILED"
    assert "rpc unavailable" in failed.error


def test_history_capture_is_noop_when_depth_is_already_ready(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_chain_observations(storage, pool, 43)

    calls = []
    report = run_phase9_history_capture(
        storage,
        inspector=lambda pool, radius: calls.append((pool, radius)),
    )

    assert report.history_ready_before is True
    assert report.history_ready_after is True
    assert report.pools_attempted == 0
    assert calls == []
    assert any(
        "already ready" in reason
        for reason in report.reasons
    )


def test_history_capture_does_not_hide_missing_pool_diversity(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b"):
        save_chain_observations(storage, pool, 42)

    report = run_phase9_history_capture(
        storage,
        inspector=lambda pool, radius: payload(pool),
        ingest_observed_at="2026-09-23T13:00:00+00:00",
    )

    assert report.history_ready_after is False
    assert any(
        "diversity must be satisfied" in reason
        for reason in report.reasons
    )


def test_history_capture_continues_sampling_after_depth_ready(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_chain_observations(storage, pool, 43)

    calls = []
    report = run_phase9_history_capture(
        storage,
        inspector=lambda pool, radius: (
            calls.append((pool, radius)) or payload(pool)
        ),
        ingest_observed_at="2026-09-23T13:00:42+00:00",
        min_observation_interval_seconds=3600,
        continue_sampling_when_ready=True,
    )

    assert report.history_ready_before is True
    assert report.history_ready_after is True
    assert report.continue_sampling_when_ready is True
    assert report.pools_attempted == 3
    assert report.pools_captured == 3
    assert report.observations_remaining_after == 0
    assert calls == [
        ("pool-a", 1),
        ("pool-b", 1),
        ("pool-c", 1),
    ]
    assert all(
        item.observations_before == 43
        and item.observations_after == 44
        and item.additional_observations_needed_after == 0
        for item in report.items
    )
    assert any(
        "ongoing cadence sampling remained enabled" in reason
        for reason in report.reasons
    )


def test_history_capture_ready_sampling_still_honors_interval(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_chain_observations(storage, pool, 43)

    calls = []
    report = run_phase9_history_capture(
        storage,
        inspector=lambda pool, radius: (
            calls.append((pool, radius)) or payload(pool)
        ),
        ingest_observed_at="2026-09-23T12:30:42+00:00",
        min_observation_interval_seconds=3600,
        continue_sampling_when_ready=True,
    )

    assert calls == []
    assert report.history_ready_after is True
    assert report.pools_attempted == 3
    assert report.pools_captured == 0
    assert report.pools_skipped_interval == 3
    assert all(
        item.status == "SKIPPED_INTERVAL"
        for item in report.items
    )


def test_history_capture_samples_ready_and_shallow_explicit_cohort(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_chain_observations(storage, pool, 43)
    save_chain_observations(storage, "pool-d", 1)

    calls = []
    report = run_phase9_history_capture(
        storage,
        pool_addresses=("pool-a", "pool-b", "pool-c", "pool-d"),
        inspector=lambda pool, radius: (
            calls.append(pool) or payload(pool)
        ),
        ingest_observed_at="2026-09-23T13:00:42+00:00",
        min_observation_interval_seconds=3600,
        continue_sampling_when_ready=True,
    )

    assert report.history_ready_before is True
    assert report.history_ready_after is True
    assert report.pools_attempted == 4
    assert report.pools_captured == 4
    assert calls == ["pool-a", "pool-b", "pool-c", "pool-d"]
    shallow = next(
        item for item in report.items
        if item.pool_address == "pool-d"
    )
    assert shallow.observations_before == 1
    assert shallow.observations_after == 2
    assert shallow.additional_observations_needed_after == 41
