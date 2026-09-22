from meteora_learner.collector import extract_pool_rows, pool_address


def test_extract_pool_rows_supports_common_envelopes():
    assert extract_pool_rows({"data": [{"address": "a"}]}) == [{"address": "a"}]
    assert extract_pool_rows({"pools": [{"address": "b"}]}) == [{"address": "b"}]
    assert extract_pool_rows([{"address": "c"}]) == [{"address": "c"}]


def test_pool_address_fallbacks():
    assert pool_address({"address": "a"}) == "a"
    assert pool_address({"pool_address": "b"}) == "b"
    assert pool_address({"lb_pair": "c"}) == "c"
    assert pool_address({}) is None
