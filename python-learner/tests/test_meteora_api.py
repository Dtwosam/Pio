import pytest

from meteora_learner.meteora_api import MeteoraDataAPI


def test_sort_by_requires_direction():
    api = MeteoraDataAPI(max_retries=0)
    try:
        with pytest.raises(ValueError):
            api.pools(sort_by="tvl")
    finally:
        api.close()


def test_ohlcv_maps_current_timeseries_parameters():
    api = MeteoraDataAPI(max_retries=0)
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"data": []}

    api._get = fake_get  # type: ignore[method-assign]
    try:
        api.ohlcv("pool", start_time=10, end_time=20, timeframe="5m")
    finally:
        api.close()

    assert captured["path"] == "/pools/pool/ohlcv"
    assert captured["params"] == {"timeframe": "5m", "start_time": 10, "end_time": 20}


def test_volume_history_uses_same_timeseries_contract():
    api = MeteoraDataAPI(max_retries=0)
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"data": []}

    api._get = fake_get  # type: ignore[method-assign]
    try:
        api.volume_history("pool", start_time=10, end_time=20, timeframe="1h")
    finally:
        api.close()

    assert captured["path"] == "/pools/pool/volume/history"
    assert captured["params"] == {"timeframe": "1h", "start_time": 10, "end_time": 20}


def test_timeseries_rejects_bad_timeframe_and_window():
    api = MeteoraDataAPI(max_retries=0)
    try:
        with pytest.raises(ValueError):
            api.ohlcv("pool", timeframe="15m")
        with pytest.raises(ValueError):
            api.ohlcv("pool", start_time=20, end_time=10)
    finally:
        api.close()


def test_position_pnl_maps_required_wallet_and_filters():
    api = MeteoraDataAPI(max_retries=0)
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"data": []}

    api._get = fake_get  # type: ignore[method-assign]
    try:
        api.position_pnl("pool", user="wallet", status="closed", page=2, page_size=50)
    finally:
        api.close()

    assert captured["path"] == "/positions/pool/pnl"
    assert captured["params"] == {
        "user": "wallet",
        "status": "closed",
        "page": 2,
        "page_size": 50,
    }


def test_pool_position_addresses_paginates_status_all_and_deduplicates():
    api = MeteoraDataAPI(max_retries=0)
    calls = []

    def fake_position_pnl(
        pool_address,
        *,
        user,
        status=None,
        page=1,
        page_size=20,
    ):
        calls.append((pool_address, user, status, page, page_size))
        if page == 1:
            return {
                "positions": [
                    {"positionAddress": "position-a"},
                    {"positionAddress": "position-b"},
                ],
                "hasNext": True,
            }
        return {
            "positions": [
                {"positionAddress": "position-b"},
                {"positionAddress": "position-c"},
            ],
            "hasNext": False,
        }

    api.position_pnl = fake_position_pnl  # type: ignore[method-assign]
    try:
        result = api.pool_position_addresses(
            "pool",
            user="wallet",
            max_pages=3,
            page_size=100,
        )
    finally:
        api.close()

    assert result == (
        "position-a",
        "position-b",
        "position-c",
    )
    assert calls == [
        ("pool", "wallet", "all", 1, 100),
        ("pool", "wallet", "all", 2, 100),
    ]


def test_pool_position_addresses_stops_at_max_pages():
    api = MeteoraDataAPI(max_retries=0)
    calls = []

    def fake_position_pnl(*args, page=1, **kwargs):
        calls.append(page)
        return {
            "positions": [
                {"positionAddress": f"position-{page}"}
            ],
            "hasNext": True,
        }

    api.position_pnl = fake_position_pnl  # type: ignore[method-assign]
    try:
        result = api.pool_position_addresses(
            "pool",
            user="wallet",
            max_pages=2,
        )
    finally:
        api.close()

    assert result == ("position-1", "position-2")
    assert calls == [1, 2]


def test_pool_position_addresses_rejects_malformed_response():
    api = MeteoraDataAPI(max_retries=0)
    api.position_pnl = (  # type: ignore[method-assign]
        lambda *args, **kwargs: {"hasNext": False}
    )
    try:
        with pytest.raises(Exception, match="positions array"):
            api.pool_position_addresses(
                "pool",
                user="wallet",
            )
    finally:
        api.close()


def test_position_history_maps_filters():
    api = MeteoraDataAPI(max_retries=0)
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"data": []}

    api._get = fake_get  # type: ignore[method-assign]
    try:
        api.position_history("position", event_type="add", order_direction="asc")
    finally:
        api.close()

    assert captured["path"] == "/positions/position/historical"
    assert captured["params"] == {"event_type": "add", "order_direction": "asc"}
