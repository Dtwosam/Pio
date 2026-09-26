from __future__ import annotations

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.market_mint_refresh import (
    build_market_mint_refresh_queue,
    run_market_mint_refresh,
)
from meteora_learner.storage import Storage


def _pool(pool: str, x: str, y: str) -> dict:
    return {
        "pool_address": pool,
        "active_bin_id": 0,
        "bin_step": 25,
        "token_x_mint": x,
        "token_y_mint": y,
        "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
        "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
        "bin_arrays": [],
    }


def _mint(
    mint: str,
    *,
    supply: str = "1000000000",
    authority: str | None = None,
) -> dict:
    return {
        "mint_address": mint,
        "token_program": STANDARD_SPL_TOKEN_PROGRAM,
        "capture_slot_start": 100,
        "capture_slot_end": 101,
        "supply": supply,
        "decimals": 6,
        "is_initialized": True,
        "mint_authority": authority,
        "freeze_authority": None,
        "data_len": 82,
        "token_2022_extension_data_len": 0,
        "has_token_2022_extension_data": False,
    }


def _seed(storage: Storage) -> None:
    storage.save_chain_pool_snapshot(
        _pool("A", "SHARED", "A-Y"),
        observed_at="2026-01-01T00:00:00+00:00",
    )
    storage.save_chain_pool_snapshot(
        _pool("B", "SHARED", "B-Y"),
        observed_at="2026-01-01T01:00:00+00:00",
    )
    storage.save_token_mint_snapshot(
        _mint("SHARED"),
        observed_at="2026-01-01T02:00:00+00:00",
    )
    storage.save_token_mint_snapshot(
        _mint("A-Y"),
        observed_at="2026-01-01T03:00:00+00:00",
    )
    storage.save_token_mint_snapshot(
        _mint("B-Y"),
        observed_at="2026-01-01T04:00:00+00:00",
    )


def test_mint_refresh_queue_is_oldest_observation_first(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage)

    queue = build_market_mint_refresh_queue(
        storage,
        batch_limit=3,
    )

    assert queue.required_mints == 3
    assert queue.observed_mints == 3
    assert [
        item.mint_address for item in queue.candidates
    ] == ["SHARED", "A-Y", "B-Y"]
    shared = queue.candidates[0]
    assert shared.pools == ("A", "B")
    assert shared.roles == ("TOKEN_X",)
    assert queue.policy_actionable is False


def test_mint_refresh_records_longitudinal_snapshot(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage)
    calls: list[str] = []

    def inspector(mint: str) -> dict:
        calls.append(mint)
        return _mint(
            mint,
            supply="2000000000",
            authority="NEW_AUTH" if mint == "SHARED" else None,
        )

    report = run_market_mint_refresh(
        storage,
        batch_limit=2,
        inspector=inspector,
        observed_at="2026-01-02T00:00:00+00:00",
    )

    assert calls == ["SHARED", "A-Y"]
    assert report.mints_refreshed == 2
    assert report.mints_failed == 0
    assert report.items[0].new_observed_at == (
        "2026-01-02T00:00:00+00:00"
    )

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*), MAX(supply), MAX(mint_authority)
            FROM token_mint_snapshots
            WHERE mint_address = 'SHARED'
            """
        ).fetchone()
    assert int(row[0]) == 2
    assert str(row[1]) == "2000000000"
    assert str(row[2]) == "NEW_AUTH"


def test_same_cycle_timestamp_is_not_immediately_refreshed_again(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage)
    timestamp = "2026-01-02T00:00:00+00:00"

    first = run_market_mint_refresh(
        storage,
        batch_limit=3,
        inspector=lambda mint: _mint(mint),
        observed_at=timestamp,
    )
    second = run_market_mint_refresh(
        storage,
        batch_limit=3,
        inspector=lambda mint: (_ for _ in ()).throw(
            AssertionError("same-cycle mint should not refresh twice")
        ),
        observed_at=timestamp,
    )

    assert first.mints_refreshed == 3
    assert second.mints_attempted == 0
    assert second.mints_refreshed == 0
