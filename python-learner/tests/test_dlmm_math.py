from decimal import Decimal

from meteora_learner.dlmm_math import bin_price, price_to_bin_id, range_prices


def test_bin_price_matches_meteora_formula():
    assert bin_price(0, 25) == Decimal("1")
    assert bin_price(1, 25) == Decimal("1.0025")


def test_price_bin_round_trip():
    for bin_id in (-100, -1, 0, 1, 100):
        price = bin_price(bin_id, 10)
        assert price_to_bin_id(price, 10, round_down=True) == bin_id
        assert price_to_bin_id(price, 10, round_down=False) == bin_id


def test_range_prices_rejects_inverted_range():
    try:
        range_prices(10, 9, 25)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
