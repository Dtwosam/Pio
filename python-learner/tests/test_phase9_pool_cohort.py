from meteora_learner.phase9_pool_cohort import (
    Phase9PoolCohortCriteria,
    evaluate_phase9_pool_cohort,
)
from meteora_learner.storage import Storage


def seed_api(storage, pool, tvl, volume):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO pool_snapshots(
                observed_at, address, name, tvl,
                volume_24h, fees_24h, raw_json
            ) VALUES (
                '2026-09-23T12:00:00+00:00',
                ?, ?, ?, ?, 1, '{}'
            )
            """,
            (pool, pool, tvl, volume),
        )


def seed_chain(storage, pool, count):
    for index in range(count):
        storage.save_chain_pool_snapshot(
            {
                "pool_address": pool,
                "active_bin_id": index % 3,
                "bin_step": 25,
                "token_x_mint": f"{pool}-x",
                "token_y_mint": f"{pool}-y",
                "bin_arrays": [],
            },
            observed_at=(
                "2026-09-23T"
                f"{index // 60:02d}:{index % 60:02d}:00+00:00"
            ),
        )


def test_pool_cohort_keeps_ready_incumbent_while_replacement_is_shallow(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for rank, pool in enumerate(
        ("pool-a", "pool-b", "pool-c", "pool-d", "pool-e", "pool-f"),
    ):
        seed_api(
            storage,
            pool,
            tvl=1_000 - rank * 100,
            volume=500 - rank * 10,
        )

    for pool in ("pool-a", "pool-b", "pool-c", "pool-f"):
        seed_chain(storage, pool, 43)
    seed_chain(storage, "pool-d", 1)

    report = evaluate_phase9_pool_cohort(storage)

    assert report.required_observations == 43
    assert report.desired_pools == (
        "pool-a",
        "pool-b",
        "pool-c",
        "pool-d",
        "pool-e",
    )
    assert report.missing_chain_pools == ("pool-e",)
    assert report.research_pools == (
        "pool-a",
        "pool-b",
        "pool-c",
        "pool-f",
    )
    assert report.sampling_pools == (
        "pool-a",
        "pool-b",
        "pool-c",
        "pool-d",
        "pool-f",
    )
    assert report.research_ready is True
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_pool_cohort_promotes_ranked_replacement_after_history_is_ready(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for rank, pool in enumerate(
        ("pool-a", "pool-b", "pool-c", "pool-d", "pool-e", "pool-f"),
    ):
        seed_api(
            storage,
            pool,
            tvl=1_000 - rank * 100,
            volume=500 - rank * 10,
        )
    for pool in ("pool-a", "pool-b", "pool-c", "pool-d", "pool-e", "pool-f"):
        seed_chain(storage, pool, 43)

    report = evaluate_phase9_pool_cohort(storage)

    assert report.research_pools == (
        "pool-a",
        "pool-b",
        "pool-c",
        "pool-d",
        "pool-e",
    )
    assert report.sampling_pools == report.research_pools
    assert report.missing_chain_pools == ()
    assert report.research_ready is True


def test_pool_cohort_falls_back_to_chain_depth_when_api_is_empty(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_chain(storage, "pool-b", 44)
    seed_chain(storage, "pool-a", 43)
    seed_chain(storage, "pool-c", 42)

    report = evaluate_phase9_pool_cohort(
        storage,
        criteria=Phase9PoolCohortCriteria(
            min_research_pools=2,
            target_pools=3,
            max_sampling_pools=4,
        ),
    )

    assert report.api_pools_seen == 0
    assert report.desired_pools == (
        "pool-b",
        "pool-a",
        "pool-c",
    )
    assert report.research_pools == ("pool-b", "pool-a")
    assert report.sampling_pools == (
        "pool-b",
        "pool-a",
        "pool-c",
    )
    assert report.research_ready is True


def test_pool_cohort_limits_sampling_union(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for rank, pool in enumerate(
        ("pool-a", "pool-b", "pool-c", "pool-d", "pool-e", "pool-f"),
    ):
        seed_api(
            storage,
            pool,
            tvl=1_000 - rank * 100,
            volume=500 - rank * 10,
        )
    for pool in ("pool-c", "pool-d", "pool-e", "pool-f"):
        seed_chain(storage, pool, 43)
    seed_chain(storage, "pool-a", 1)
    seed_chain(storage, "pool-b", 1)

    report = evaluate_phase9_pool_cohort(
        storage,
        criteria=Phase9PoolCohortCriteria(
            min_research_pools=3,
            target_pools=5,
            max_sampling_pools=5,
        ),
    )

    assert len(report.sampling_pools) == 5
    assert report.sampling_pools[:4] == (
        "pool-a",
        "pool-b",
        "pool-c",
        "pool-d",
    )


def test_pool_cohort_excludes_stale_api_rankings_at_live_cutoff(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for rank, pool in enumerate(("pool-a", "pool-b", "pool-c")):
        seed_api(
            storage,
            pool,
            tvl=1_000 - rank * 100,
            volume=500 - rank * 10,
        )
        seed_chain(storage, pool, 43)

    report = evaluate_phase9_pool_cohort(
        storage,
        criteria=Phase9PoolCohortCriteria(
            min_research_pools=3,
            target_pools=3,
            max_sampling_pools=3,
            max_api_snapshot_age_seconds=3_600,
        ),
        as_of="2026-09-23T16:00:00+00:00",
    )

    assert report.api_pools_seen == 0
    assert report.stale_api_pools_excluded == 3
    assert report.api_ranking_as_of == "2026-09-23T16:00:00+00:00"
    assert report.desired_pools == ("pool-a", "pool-b", "pool-c")
    assert report.research_pools == ("pool-a", "pool-b", "pool-c")
    assert report.research_ready is True
    assert any(
        "stale API pool ranking snapshot" in reason
        for reason in report.reasons
    )


def test_pool_cohort_historical_cutoff_uses_latest_pre_cutoff_api_row(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_api(
        storage,
        "pool-a",
        tvl=500,
        volume=50,
    )
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO pool_snapshots(
                observed_at, address, name, tvl,
                volume_24h, fees_24h, raw_json
            ) VALUES (
                '2026-09-23T18:00:00+00:00',
                'pool-a', 'pool-a', 5000, 5000, 1, '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO pool_snapshots(
                observed_at, address, name, tvl,
                volume_24h, fees_24h, raw_json
            ) VALUES (
                '2026-09-23T13:00:00+00:00',
                'pool-b', 'pool-b', 400, 40, 1, '{}'
            )
            """
        )
    seed_chain(storage, "pool-a", 43)
    seed_chain(storage, "pool-b", 43)

    report = evaluate_phase9_pool_cohort(
        storage,
        criteria=Phase9PoolCohortCriteria(
            min_research_pools=2,
            target_pools=2,
            max_sampling_pools=2,
            max_api_snapshot_age_seconds=7_200,
        ),
        as_of="2026-09-23T14:00:00+00:00",
    )

    assert report.api_pools_seen == 2
    assert report.stale_api_pools_excluded == 0
    assert report.desired_pools == ("pool-a", "pool-b")
    pool_a = next(
        item for item in report.items
        if item.pool_address == "pool-a"
    )
    assert pool_a.api_observed_at == "2026-09-23T12:00:00+00:00"
    assert pool_a.tvl == 500.0
