from __future__ import annotations

from meteora_learner.market_pool_universe import (
    discover_pool_universe,
)
from meteora_learner.research_store import ResearchStore
from meteora_learner.storage import Storage


def _pool(address: str, *, tvl: float) -> dict:
    return {
        "address": address,
        "name": f"{address}-pool",
        "tvl": tvl,
        "volume_24h": tvl * 0.25,
        "fees_24h": tvl * 0.001,
        "current_price": 1.0,
        "bin_step": 25,
        "active_bin_id": 0,
        "token_x": {"symbol": "X", "decimals": 6},
        "token_y": {"symbol": "Y", "decimals": 6},
    }


def test_discovery_paginates_deduplicates_and_persists(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    calls: list[tuple[int, int]] = []

    pages = {
        1: {"data": [_pool("A", tvl=100), _pool("B", tvl=90)]},
        2: {"data": [_pool("B", tvl=90), _pool("C", tvl=80)]},
        3: {"data": []},
    }

    def fetch_page(page: int, page_size: int):
        calls.append((page, page_size))
        return pages[page]

    report = discover_pool_universe(
        storage,
        observed_at="2026-09-26T00:00:00+00:00",
        fetch_page=fetch_page,
        page_size=2,
        max_pages=10,
    )

    assert calls == [(1, 2), (2, 2), (3, 2)]
    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.pages_fetched == 3
    assert report.raw_rows_seen == 4
    assert report.unique_pools_seen == 3
    assert report.snapshots_saved == 3
    assert report.duplicate_rows == 1
    assert report.invalid_rows == 0
    assert report.stop_reason == "EMPTY_PAGE"

    rows = ResearchStore(str(storage.path)).latest_pool_snapshots()
    assert [row["address"] for row in rows] == ["A", "B", "C"]


def test_discovery_stops_on_short_page(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    calls: list[int] = []

    def fetch_page(page: int, page_size: int):
        calls.append(page)
        if page == 1:
            return {"pools": [_pool("A", tvl=100)]}
        raise AssertionError("short page should stop pagination")

    report = discover_pool_universe(
        storage,
        fetch_page=fetch_page,
        page_size=2,
        max_pages=10,
    )

    assert calls == [1]
    assert report.stop_reason == "SHORT_PAGE"
    assert report.unique_pools_seen == 1


def test_discovery_stops_when_full_page_has_no_new_pools(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")

    pages = {
        1: {"data": [_pool("A", tvl=100), _pool("B", tvl=90)]},
        2: {"data": [_pool("A", tvl=100), _pool("B", tvl=90)]},
    }

    report = discover_pool_universe(
        storage,
        fetch_page=lambda page, page_size: pages[page],
        page_size=2,
        max_pages=10,
    )

    assert report.pages_fetched == 2
    assert report.unique_pools_seen == 2
    assert report.duplicate_rows == 2
    assert report.stop_reason == "NO_NEW_POOLS"


def test_discovery_keeps_missing_address_as_data_quality_issue(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")

    report = discover_pool_universe(
        storage,
        fetch_page=lambda page, page_size: {
            "data": [
                _pool("A", tvl=100),
                {"name": "missing-address"},
            ]
        },
        page_size=10,
        max_pages=2,
    )

    assert report.unique_pools_seen == 1
    assert report.snapshots_saved == 1
    assert report.invalid_rows == 1
    assert report.stop_reason == "SHORT_PAGE"


def test_discovery_has_no_economic_filter_arguments(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")

    report = discover_pool_universe(
        storage,
        fetch_page=lambda page, page_size: {
            "data": [
                _pool("tiny", tvl=1),
                _pool("large", tvl=1_000_000),
            ]
        },
        page_size=10,
        max_pages=1,
    )

    assert report.unique_pools_seen == 2
    assert report.snapshots_saved == 2
