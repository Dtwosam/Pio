from meteora_learner.phase9_capture_plan import Phase9ChainCaptureCriteria
from meteora_learner.phase9_chain_capture import run_phase9_chain_capture_batch
from meteora_learner.storage import Storage


def seed_api_pool(storage, address, *, tvl):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO pool_snapshots(
                observed_at, address, name, tvl,
                volume_24h, fees_24h, raw_json
            ) VALUES (
                '2026-09-23T12:00:00+00:00',
                ?, ?, ?, 100, 1, '{}'
            )
            """,
            (address, address, tvl),
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


def test_chain_capture_batch_reaches_target_with_read_only_inspector(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool, tvl in (
        ("pool-a", 1_000.0),
        ("pool-b", 900.0),
        ("pool-c", 800.0),
    ):
        seed_api_pool(storage, pool, tvl=tvl)

    calls = []

    def inspector(pool, radius):
        calls.append((pool, radius))
        return payload(pool)

    report = run_phase9_chain_capture_batch(
        storage,
        criteria=Phase9ChainCaptureCriteria(
            target_chain_pools=3,
            max_candidates=3,
            bin_array_radius=2,
        ),
        inspector=inspector,
        ingest_observed_at="2026-09-23T13:00:00+00:00",
    )

    assert report.target_met is True
    assert report.pools_attempted == 3
    assert report.pools_captured == 3
    assert report.pools_failed == 0
    assert report.current_chain_pool_count_before == 0
    assert report.current_chain_pool_count_after == 3
    assert calls == [
        ("pool-a", 2),
        ("pool-b", 2),
        ("pool-c", 2),
    ]
    assert report.research_only is True
    assert report.read_only_capture is True
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_chain_capture_batch_isolates_pool_failure(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for pool, tvl in (
        ("pool-a", 1_000.0),
        ("pool-b", 900.0),
    ):
        seed_api_pool(storage, pool, tvl=tvl)

    def inspector(pool, radius):
        if pool == "pool-a":
            raise RuntimeError("rpc unavailable")
        return payload(pool)

    report = run_phase9_chain_capture_batch(
        storage,
        criteria=Phase9ChainCaptureCriteria(
            target_chain_pools=2,
            max_candidates=2,
        ),
        inspector=inspector,
    )

    assert report.target_met is False
    assert report.pools_attempted == 2
    assert report.pools_captured == 1
    assert report.pools_failed == 1
    failed = next(
        item for item in report.items
        if item.pool_address == "pool-a"
    )
    assert failed.status == "FAILED"
    assert "rpc unavailable" in failed.error
    assert any(
        "remains below target" in reason
        for reason in report.reasons
    )


def test_chain_capture_batch_rejects_mismatched_inspector_pool(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_api_pool(storage, "pool-a", tvl=1_000.0)

    report = run_phase9_chain_capture_batch(
        storage,
        criteria=Phase9ChainCaptureCriteria(
            target_chain_pools=1,
            max_candidates=1,
        ),
        inspector=lambda pool, radius: payload("different-pool"),
    )

    assert report.target_met is False
    assert report.pools_captured == 0
    assert report.pools_failed == 1
    assert "different pool_address" in report.items[0].error


def test_chain_capture_batch_is_noop_when_target_already_met(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_api_pool(storage, "pool-a", tvl=1_000.0)
    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool-a",
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": "x",
            "token_y_mint": "y",
            "bin_arrays": [],
        },
        observed_at="2026-09-23T12:00:00+00:00",
    )

    calls = []

    report = run_phase9_chain_capture_batch(
        storage,
        criteria=Phase9ChainCaptureCriteria(
            target_chain_pools=1,
        ),
        inspector=lambda pool, radius: calls.append((pool, radius)),
    )

    assert report.target_met is True
    assert report.pools_attempted == 0
    assert calls == []
    assert any(
        "already satisfied" in reason
        for reason in report.reasons
    )
