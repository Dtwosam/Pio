from __future__ import annotations

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.market_chain_refresh import (
    build_market_chain_refresh_queue,
    run_market_chain_refresh,
)
from meteora_learner.storage import Storage


def _save_pool(storage: Storage, address: str, observed_at: str, tvl: float) -> None:
    storage.save_pool_snapshot(
        {
            "address": address,
            "name": address,
            "tvl": tvl,
            "volume_24h": tvl * 2,
            "fees_24h": tvl / 100,
            "current_price": 1.0,
        },
        observed_at=observed_at,
    )


def _chain_payload(pool: str, active: int = 0) -> dict:
    return {
        "pool_address": pool,
        "active_bin_id": active,
        "bin_step": 25,
        "token_x_mint": f"{pool}-X",
        "token_y_mint": f"{pool}-Y",
        "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
        "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
        "base_fee_rate": "0",
        "variable_fee_rate": "0",
        "total_fee_rate": "0",
        "deposit_total_fee_rate": "0",
        "protocol_share_bps": 0,
        "collect_fee_mode": 0,
        "supports_limit_order": True,
        "bin_arrays": [],
    }


def test_refresh_queue_is_oldest_chain_first_not_highest_tvl(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save_pool(
        storage,
        "OLD",
        "2026-01-03T00:00:00+00:00",
        tvl=10.0,
    )
    _save_pool(
        storage,
        "NEW",
        "2026-01-01T00:00:00+00:00",
        tvl=1_000_000.0,
    )
    storage.save_chain_pool_snapshot(
        _chain_payload("OLD"),
        observed_at="2026-01-01T00:00:00+00:00",
    )
    storage.save_chain_pool_snapshot(
        _chain_payload("NEW"),
        observed_at="2026-01-02T00:00:00+00:00",
    )

    queue = build_market_chain_refresh_queue(
        storage,
        batch_limit=2,
    )

    assert tuple(
        item.pool_address for item in queue.candidates
    ) == ("OLD", "NEW")
    assert queue.research_only is True
    assert queue.policy_actionable is False
    assert queue.execution_wired is False


def test_refresh_captures_existing_pool_again(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save_pool(
        storage,
        "POOL",
        "2026-01-01T00:00:00+00:00",
        tvl=100.0,
    )
    storage.save_chain_pool_snapshot(
        _chain_payload("POOL"),
        observed_at="2026-01-01T00:05:00+00:00",
    )

    calls: list[tuple[str, int]] = []

    def inspect(pool: str, radius: int) -> dict:
        calls.append((pool, radius))
        return _chain_payload(pool, active=1)

    report = run_market_chain_refresh(
        storage,
        batch_limit=1,
        bin_array_radius=2,
        inspector=inspect,
        ingest_observed_at="2026-01-02T00:00:00+00:00",
    )

    assert calls == [("POOL", 2)]
    assert report.pools_attempted == 1
    assert report.pools_refreshed == 1
    assert report.pools_failed == 0
    assert report.items[0].status == "REFRESHED"

    with storage.connect() as conn:
        times = conn.execute(
            """
            SELECT observed_at
            FROM chain_pool_snapshots
            WHERE pool_address = 'POOL'
            ORDER BY julianday(observed_at)
            """
        ).fetchall()
    # Avoid relying on any update path: refresh creates a second immutable row.
    assert [str(row[0]) for row in times] == [
        "2026-01-01T00:05:00+00:00",
        "2026-01-02T00:00:00+00:00",
    ]


def test_refresh_queue_can_exclude_same_capture_timestamp(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save_pool(
        storage,
        "POOL",
        "2026-01-01T00:00:00+00:00",
        tvl=100.0,
    )
    storage.save_chain_pool_snapshot(
        _chain_payload("POOL"),
        observed_at="2026-01-02T00:00:00+00:00",
    )

    queue = build_market_chain_refresh_queue(
        storage,
        batch_limit=1,
        exclude_last_observed_at="2026-01-02T00:00:00+00:00",
    )

    assert queue.candidates == ()
