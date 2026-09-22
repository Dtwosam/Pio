import pytest

from meteora_learner.candidates import generate_range_candidates
from meteora_learner.strategy import StrategyType


def test_candidate_grid_combines_widths_and_strategies():
    candidates = generate_range_candidates(
        pool_address="pool",
        active_bin_id=100,
        current_price=10.0,
        bin_step=25,
        capital_quote=100.0,
        half_widths=(1, 5),
        strategies=(StrategyType.SPOT, StrategyType.CURVE),
    )

    assert len(candidates) == 4
    narrow = candidates[0]
    assert narrow.min_bin_id == 99
    assert narrow.max_bin_id == 101
    assert narrow.width_bins == 3
    assert narrow.lower_price < 10.0 < narrow.upper_price


def test_candidate_grid_supports_skewed_ranges():
    candidates = generate_range_candidates(
        pool_address="pool",
        active_bin_id=0,
        current_price=1.0,
        bin_step=100,
        capital_quote=50.0,
        half_widths=(2,),
        strategies=(StrategyType.BID_ASK,),
        center_offsets=(-2, 0, 2),
    )

    assert [(c.min_bin_id, c.max_bin_id) for c in candidates] == [(-4, 0), (-2, 2), (0, 4)]
    assert candidates[0].upper_price == pytest.approx(1.0)
