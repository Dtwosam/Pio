from __future__ import annotations

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.market_context_collection_cycle import (
    run_market_context_collection_cycle,
)
from meteora_learner.storage import Storage


def _api_pool(address: str) -> dict:
    return {
        "address": address,
        "name": address,
        "tvl": 100_000.0,
        "volume_24h": 20_000.0,
        "fees_24h": 100.0,
        "current_price": 1.0,
        "bin_step": 25,
        "active_bin_id": 0,
        "token_x": {"symbol": "X", "decimals": 6},
        "token_y": {"symbol": "Y", "decimals": 6},
    }


def _pool_payload(pool: str) -> dict:
    return {
        "pool_address": pool,
        "active_bin_id": 0,
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


def test_collection_cycle_discovers_then_captures_chain_then_mints(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    order: list[str] = []

    def fetch_page(page: int, page_size: int):
        order.append(f"discover:{page}")
        return {"data": [_api_pool("POOL")]}

    def pool_inspector(pool: str, radius: int):
        order.append(f"pool:{pool}")
        return _pool_payload(pool)

    def mint_inspector(mint: str):
        order.append(f"mint:{mint}")
        return _mint_payload(mint)

    report = run_market_context_collection_cycle(
        storage,
        observed_at="2026-09-26T00:00:00+00:00",
        fetch_page=fetch_page,
        pool_inspector=pool_inspector,
        mint_inspector=mint_inspector,
        page_size=10,
        max_pages=1,
        chain_batch_limit=5,
        mint_batch_limit=10,
    )

    assert order == [
        "discover:1",
        "pool:POOL",
        "mint:POOL-X",
        "mint:POOL-Y",
    ]
    assert report.status == "CAPTURED"
    assert report.pools_discovered == 1
    assert report.chain_pools_captured == 1
    assert report.chain_pools_failed == 0
    assert report.mints_captured == 2
    assert report.mints_failed == 0
    assert report.research_only is True
    assert report.read_only_capture is True
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_collection_cycle_is_no_change_when_context_complete(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    storage.save_pool_snapshot(
        _api_pool("POOL"),
        observed_at="2026-09-26T00:00:00+00:00",
    )
    storage.save_chain_pool_snapshot(
        _pool_payload("POOL"),
        observed_at="2026-09-26T00:00:00+00:00",
    )
    for mint in ("POOL-X", "POOL-Y"):
        storage.save_token_mint_snapshot(
            _mint_payload(mint),
            observed_at="2026-09-26T00:00:00+00:00",
        )

    report = run_market_context_collection_cycle(
        storage,
        capture_universe=False,
        pool_inspector=lambda pool, radius: (
            _ for _ in ()
        ).throw(AssertionError("pool inspector should not run")),
        mint_inspector=lambda mint: (
            _ for _ in ()
        ).throw(AssertionError("mint inspector should not run")),
    )

    assert report.status == "NO_CHANGE"
    assert report.chain_pools_captured == 0
    assert report.mints_captured == 0


def test_collection_cycle_isolates_capture_failures(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")

    report = run_market_context_collection_cycle(
        storage,
        observed_at="2026-09-26T00:00:00+00:00",
        fetch_page=lambda page, page_size: {
            "data": [_api_pool("POOL")]
        },
        pool_inspector=lambda pool, radius: (
            _ for _ in ()
        ).throw(RuntimeError("rpc unavailable")),
        mint_inspector=lambda mint: _mint_payload(mint),
        page_size=10,
        max_pages=1,
    )

    assert report.status == "PARTIAL"
    assert report.chain_pools_failed == 1
    assert report.mints_captured == 0


def test_collection_cycle_can_run_discovery_only(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")

    report = run_market_context_collection_cycle(
        storage,
        capture_chain_context=False,
        capture_mint_context=False,
        fetch_page=lambda page, page_size: {
            "data": [_api_pool("POOL")]
        },
        page_size=10,
        max_pages=1,
    )

    assert report.status == "DISCOVERED"
    assert report.discovery is not None
    assert report.chain_context is None
    assert report.mint_context is None
