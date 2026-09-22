from meteora_learner.strategy import StrategyType, strategy_weights


def test_spot_is_uniform():
    assert strategy_weights(-2, 2, 0, StrategyType.SPOT) == {-2: 1, -1: 1, 0: 1, 1: 1, 2: 1}


def test_curve_weights_toward_active_bin():
    weights = strategy_weights(-2, 2, 0, StrategyType.CURVE)
    assert weights[0] == 2000
    assert weights[-2] <= weights[-1] < weights[0]
    assert weights[2] <= weights[1] < weights[0]


def test_bid_ask_weights_toward_edges():
    weights = strategy_weights(-2, 2, 0, StrategyType.BID_ASK)
    assert weights[0] == 200
    assert weights[-2] >= weights[-1] > weights[0]
    assert weights[2] >= weights[1] > weights[0]
