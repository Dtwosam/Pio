from __future__ import annotations

from meteora_learner.market_chain_context import (
    build_market_chain_context_queue,
    run_market_chain_context_capture,
)
from meteora_learner.storage import Storage


def _api_pool(
    storage: Storage,
    address: str,
    observed_at: str,
    tvl: float,
) -> None:
    storage.save_pool_snapshot(
        {
            "address": address,
            "tvl": tvl,
            "volume_24h": tvl * 100,
            "fees_24h": tvl * 10,
            "current_price": 1.0,
        },
        observed_at=observed_at,
    )


def _payload(pool: str) -> dict:
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
        "bin_arrays": [],
    }


def test_queue_is_oldest_discovery_first_not_tvl_ranked(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _api_pool(
        storage,
        "old-low-tvl",
        "2026-01-01T00:00:00+00:00",
        1.0,
    )
    _api_pool(
        storage,
        "new-high-tvl",
        "2026-01-02T00:00:00+00:00",
        1_000_000.0,
    )

    queue = build_market_chain_context_queue(
        storage,
        batch_limit=2,
    )

    assert [
        item.pool_address for item in queue.candidates
    ] == ["old-low-tvl", "new-high-tvl"]
    assert queue.missing_chain_pools == 2
    assert queue.policy_actionable is False


def test_queue_excludes_pool_after_chain_capture(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _api_pool(
        storage,
        "pool-a",
        "2026-01-01T00:00:00+00:00",
        100.0,
    )
    storage.save_chain_pool_snapshot(
        _payload("pool-a"),
        observed_at="2026-01-01T01:00:00+00:00",
    )

    queue = build_market_chain_context_queue(storage)

    assert queue.missing_chain_pools == 0
    assert queue.candidates == ()


def test_capture_reuses_read_only_chain_path_in_queue_order(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _api_pool(
        storage,
        "pool-b",
        "2026-01-01T00:00:00+00:00",
        1_000_000.0,
    )
    _api_pool(
        storage,
        "pool-a",
        "2026-01-01T00:00:00+00:00",
        1.0,
    )

    calls = []

    def inspector(pool: str, radius: int) -> dict:
        calls.append((pool, radius))
        return _payload(pool)

    report = run_market_chain_context_capture(
        storage,
        batch_limit=2,
        bin_array_radius=0,
        inspector=inspector,
        ingest_observed_at="2026-01-01T01:00:00+00:00",
    )

    assert calls == [
        ("pool-a", 0),
        ("pool-b", 0),
    ]
    assert report.capture is not None
    assert report.capture.pools_captured == 2
    assert report.queue_after.missing_chain_pools == 0
    assert report.research_only is True
    assert report.read_only_capture is True
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_capture_is_noop_when_no_context_is_missing(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")

    report = run_market_chain_context_capture(
        storage,
        inspector=lambda pool, radius: (_ for _ in ()).throw(
            AssertionError("inspector should not run")
        ),
    )

    assert report.capture is None
    assert report.queue_before.missing_chain_pools == 0
