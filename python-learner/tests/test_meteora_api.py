import pytest

from meteora_learner.meteora_api import MeteoraDataAPI


def test_sort_by_requires_direction():
    api = MeteoraDataAPI(max_retries=0)
    try:
        with pytest.raises(ValueError):
            api.pools(sort_by="tvl")
    finally:
        api.close()


def test_ohlcv_maps_from_parameter():
    api = MeteoraDataAPI(max_retries=0)
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"data": []}

    api._get = fake_get  # type: ignore[method-assign]
    try:
        api.ohlcv("pool", from_=10, to=20, resolution="5m")
    finally:
        api.close()

    assert captured["path"] == "/pools/pool/ohlcv"
    assert captured["params"] == {"from": 10, "to": 20, "resolution": "5m"}


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
