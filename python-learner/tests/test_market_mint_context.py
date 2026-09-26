from __future__ import annotations

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.market_mint_context import (
    build_market_mint_context_queue,
    run_market_mint_context_capture,
)
from meteora_learner.storage import Storage


def _pool_payload(pool: str, x: str, y: str) -> dict:
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


def _mint_payload(mint: str) -> dict:
    return {
        "mint_address": mint,
        "token_program": STANDARD_SPL_TOKEN_PROGRAM,
        "capture_slot_start": 100,
        "capture_slot_end": 101,
        "supply": "1000000000",
        "decimals": 6,
        "is_initialized": True,
        "mint_authority": None,
        "freeze_authority": None,
        "data_len": 82,
        "token_2022_extension_data_len": 0,
        "has_token_2022_extension_data": False,
    }


def test_mint_queue_deduplicates_shared_mints(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_pool_snapshot(
        _pool_payload("A", "SHARED", "A-Y"),
        observed_at="2026-01-01T00:00:00+00:00",
    )
    storage.save_chain_pool_snapshot(
        _pool_payload("B", "SHARED", "B-Y"),
        observed_at="2026-01-01T01:00:00+00:00",
    )

    queue = build_market_mint_context_queue(
        storage,
        batch_limit=10,
    )

    assert queue.unique_required_mints == 3
    assert queue.missing_mints == 3
    shared = next(
        item for item in queue.candidates
        if item.mint_address == "SHARED"
    )
    assert shared.pools == ("A", "B")
    assert shared.roles == ("TOKEN_X",)


def test_mint_queue_excludes_already_observed_mint(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_pool_snapshot(
        _pool_payload("A", "A-X", "A-Y"),
        observed_at="2026-01-01T00:00:00+00:00",
    )
    storage.save_token_mint_snapshot(
        _mint_payload("A-X"),
        observed_at="2026-01-01T01:00:00+00:00",
    )

    queue = build_market_mint_context_queue(storage)

    assert queue.unique_required_mints == 2
    assert queue.mints_with_any_snapshot == 1
    assert queue.missing_mints == 1
    assert [
        item.mint_address for item in queue.candidates
    ] == ["A-Y"]


def test_mint_capture_fills_missing_context(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_pool_snapshot(
        _pool_payload("A", "A-X", "A-Y"),
        observed_at="2026-01-01T00:00:00+00:00",
    )

    calls = []

    def inspector(mint: str) -> dict:
        calls.append(mint)
        return _mint_payload(mint)

    report = run_market_mint_context_capture(
        storage,
        batch_limit=10,
        inspector=inspector,
        observed_at="2026-01-01T01:00:00+00:00",
    )

    assert calls == ["A-X", "A-Y"]
    assert report.attempted == 2
    assert report.captured == 2
    assert report.failed == 0
    assert report.queue_after.missing_mints == 0
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_mint_capture_isolates_failure(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_pool_snapshot(
        _pool_payload("A", "A-X", "A-Y"),
        observed_at="2026-01-01T00:00:00+00:00",
    )

    def inspector(mint: str) -> dict:
        if mint == "A-X":
            raise RuntimeError("rpc unavailable")
        return _mint_payload(mint)

    report = run_market_mint_context_capture(
        storage,
        inspector=inspector,
        observed_at="2026-01-01T01:00:00+00:00",
    )

    assert report.captured == 1
    assert report.failed == 1
    assert report.queue_after.missing_mints == 1
