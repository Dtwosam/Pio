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
