from decimal import Decimal

from meteora_learner.dlmm_math import (
    bin_price,
    price_ratio_to_bin_delta,
    price_to_bin_id,
    range_prices,
    relative_bin_price,
)


def test_bin_price_matches_meteora_formula():
    assert bin_price(0, 25) == Decimal("1")
    assert bin_price(1, 25) == Decimal("1.0025")


def test_price_bin_round_trip_including_large_negative_ids():
    for bin_id in (-10_000, -100, -1, 0, 1, 100, 10_000):
        price = bin_price(bin_id, 10)
        assert price_to_bin_id(price, 10, round_down=True) == bin_id
        assert price_to_bin_id(price, 10, round_down=False) == bin_id


def test_relative_price_bin_round_trip_does_not_need_token_decimals():
    reference = Decimal("123.456")
    for delta in (-100, -1, 0, 1, 100):
        price = relative_bin_price(reference, delta, 25)
        assert price_ratio_to_bin_delta(price, reference, 25, round_down=True) == delta
        assert price_ratio_to_bin_delta(price, reference, 25, round_down=False) == delta


def test_range_prices_rejects_inverted_range():
    try:
        range_prices(10, 9, 25)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
