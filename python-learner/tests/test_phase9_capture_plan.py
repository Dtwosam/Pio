from meteora_learner.phase9_capture_plan import (
    Phase9ChainCaptureCriteria,
    build_phase9_chain_capture_plan,
)
from meteora_learner.storage import Storage


def seed_api_pool(storage, address, *, tvl, volume, observed_at):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO pool_snapshots(
                observed_at, address, name, tvl,
                volume_24h, fees_24h, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, '{}')
            """,
            (
                observed_at,
                address,
                address,
                tvl,
                volume,
                volume / 100.0,
            ),
        )


def seed_chain_pool(storage, address):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": address,
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": f"{address}-x",
            "token_y_mint": f"{address}-y",
            "bin_arrays": [],
        },
        observed_at="2026-09-23T12:00:00+00:00",
    )


def test_capture_plan_ranks_missing_api_pools_deterministically(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_api_pool(
        storage,
        "pool-a",
        tvl=1_000.0,
        volume=100.0,
        observed_at="2026-09-23T10:00:00+00:00",
    )
    seed_api_pool(
        storage,
        "pool-b",
        tvl=900.0,
        volume=200.0,
        observed_at="2026-09-23T10:00:00+00:00",
    )
    seed_api_pool(
        storage,
        "pool-c",
        tvl=800.0,
        volume=300.0,
        observed_at="2026-09-23T10:00:00+00:00",
    )
    seed_api_pool(
        storage,
        "pool-d",
        tvl=700.0,
        volume=400.0,
        observed_at="2026-09-23T10:00:00+00:00",
    )
    seed_chain_pool(storage, "pool-a")

    plan = build_phase9_chain_capture_plan(
        storage,
        criteria=Phase9ChainCaptureCriteria(
            target_chain_pools=3,
            max_candidates=4,
            bin_array_radius=2,
        ),
        rpc_url="https://rpc.example.invalid",
    )

    assert plan.plan_ready is True
    assert plan.capture_required is True
    assert plan.current_chain_pool_count == 1
    assert plan.additional_chain_pools_needed == 2
    assert [item.pool_address for item in plan.candidates] == [
        "pool-b",
        "pool-c",
    ]
    assert [item.rank for item in plan.candidates] == [1, 2]
    for item in plan.candidates:
        assert "inspect-pool" in item.shell_command
        assert "https://rpc.example.invalid" in item.shell_command
        assert " 2 | pio ingest-chain-snapshot --file -" in item.shell_command
        assert "submit" not in item.shell_command
        assert "sign" not in item.shell_command


def test_capture_plan_uses_latest_api_snapshot_per_pool(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_api_pool(
        storage,
        "pool-a",
        tvl=10.0,
        volume=10.0,
        observed_at="2026-09-23T09:00:00+00:00",
    )
    seed_api_pool(
        storage,
        "pool-a",
        tvl=500.0,
        volume=50.0,
        observed_at="2026-09-23T11:00:00+00:00",
    )
    seed_api_pool(
        storage,
        "pool-a",
        tvl=5_000.0,
        volume=5_000.0,
        observed_at="2026-09-23T08:00:00+00:00",
    )
    seed_api_pool(
        storage,
        "pool-b",
        tvl=100.0,
        volume=100.0,
        observed_at="2026-09-23T10:00:00+00:00",
    )

    plan = build_phase9_chain_capture_plan(
        storage,
        criteria=Phase9ChainCaptureCriteria(
            target_chain_pools=1,
            max_candidates=2,
        ),
    )

    assert plan.candidates[0].pool_address == "pool-a"
    assert plan.candidates[0].tvl == 500.0
    assert plan.candidates[0].api_observed_at == (
        "2026-09-23T11:00:00+00:00"
    )


def test_capture_plan_requests_api_collection_when_discovery_is_empty(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")

    plan = build_phase9_chain_capture_plan(storage)

    assert plan.plan_ready is False
    assert plan.api_pool_count == 0
    assert plan.candidates == ()
    assert any(
        "run collect-once" in reason
        for reason in plan.reasons
    )


def test_capture_plan_is_complete_when_chain_pool_target_is_met(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        seed_chain_pool(storage, pool)

    plan = build_phase9_chain_capture_plan(storage)

    assert plan.plan_ready is True
    assert plan.capture_required is False
    assert plan.additional_chain_pools_needed == 0
    assert plan.candidates == ()
    assert any(
        "already satisfied" in reason
        for reason in plan.reasons
    )


def test_capture_plan_onboards_preferred_pool_after_minimum_target(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for pool, tvl in (
        ("pool-a", 1_000.0),
        ("pool-b", 900.0),
        ("pool-c", 800.0),
        ("pool-d", 700.0),
        ("pool-e", 600.0),
    ):
        seed_api_pool(
            storage,
            pool,
            tvl=tvl,
            volume=100.0,
            observed_at="2026-09-23T10:00:00+00:00",
        )
    for pool in ("pool-a", "pool-b", "pool-c"):
        seed_chain_pool(storage, pool)

    plan = build_phase9_chain_capture_plan(
        storage,
        criteria=Phase9ChainCaptureCriteria(
            target_chain_pools=3,
            max_candidates=4,
        ),
        preferred_pool_addresses=(
            "pool-a", "pool-b", "pool-c", "pool-d", "pool-e"
        ),
        max_preferred_candidates=1,
    )

    assert plan.additional_chain_pools_needed == 0
    assert plan.capture_required is True
    assert plan.preferred_pool_count == 5
    assert plan.preferred_missing_chain_pools == ("pool-d", "pool-e")
    assert [item.pool_address for item in plan.candidates] == ["pool-d"]
    assert plan.plan_ready is False
    assert any(
        "ranked cohort onboarding is still required" in reason
        for reason in plan.reasons
    )
    assert any(
        "pool-e" in reason
        for reason in plan.reasons
    )


def test_capture_plan_excludes_stale_api_candidates(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_api_pool(
        storage,
        "pool-a",
        tvl=1_000.0,
        volume=100.0,
        observed_at="2026-09-23T10:00:00+00:00",
    )

    plan = build_phase9_chain_capture_plan(
        storage,
        criteria=Phase9ChainCaptureCriteria(
            target_chain_pools=1,
            max_candidates=2,
            max_api_snapshot_age_seconds=3_600,
        ),
        as_of="2026-09-23T14:00:00+00:00",
    )

    assert plan.api_pool_count == 0
    assert plan.stale_api_pool_count == 1
    assert plan.api_ranking_as_of == "2026-09-23T14:00:00+00:00"
    assert plan.candidates == ()
    assert plan.plan_ready is False
    assert any(
        "no fresh discovered API pool snapshots" in reason
        for reason in plan.reasons
    )


def test_capture_plan_historical_cutoff_ignores_future_latest_row(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_api_pool(
        storage,
        "pool-a",
        tvl=500.0,
        volume=50.0,
        observed_at="2026-09-23T12:00:00+00:00",
    )
    seed_api_pool(
        storage,
        "pool-a",
        tvl=5_000.0,
        volume=5_000.0,
        observed_at="2026-09-23T18:00:00+00:00",
    )

    plan = build_phase9_chain_capture_plan(
        storage,
        criteria=Phase9ChainCaptureCriteria(
            target_chain_pools=1,
            max_candidates=2,
            max_api_snapshot_age_seconds=7_200,
        ),
        as_of="2026-09-23T14:00:00+00:00",
    )

    assert plan.api_pool_count == 1
    assert plan.stale_api_pool_count == 0
    assert plan.candidates[0].pool_address == "pool-a"
    assert plan.candidates[0].api_observed_at == (
        "2026-09-23T12:00:00+00:00"
    )
    assert plan.candidates[0].tvl == 500.0
