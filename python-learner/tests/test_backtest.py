import pytest

from meteora_learner.backtest import evaluate_candidate, evaluate_candidate_grid
from meteora_learner.candidates import generate_range_candidates
from meteora_learner.dlmm_math import relative_bin_price
from meteora_learner.strategy import StrategyType


def test_candidate_backtest_starts_from_requested_capital():
    candidate = generate_range_candidates(
        pool_address="pool",
        active_bin_id=100,
        current_price=10.0,
        bin_step=100,
        capital_quote=100.0,
        half_widths=(2,),
        strategies=(StrategyType.SPOT,),
    )[0]

    prices = [
        10.0,
        float(relative_bin_price(10.0, 1, 100)),
        float(relative_bin_price(10.0, 2, 100)),
    ]
    result = evaluate_candidate(candidate, prices)

    assert result.simulation.initial_value_quote == pytest.approx(100.0)
    assert result.simulation.ending_active_id == 102


def test_grid_evaluates_every_strategy_width_combination():
    candidates = generate_range_candidates(
        pool_address="pool",
        active_bin_id=0,
        current_price=1.0,
        bin_step=25,
        capital_quote=100.0,
        half_widths=(1, 5),
        strategies=(StrategyType.SPOT, StrategyType.CURVE, StrategyType.BID_ASK),
    )

    results = evaluate_candidate_grid(candidates, [1.0, 1.001, 1.002])
    assert len(results) == 6
    assert {item.strategy for item in results} == {"SPOT", "CURVE", "BID_ASK"}
