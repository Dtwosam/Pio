from meteora_learner.strategy import StrategyType, deposit_weights, strategy_weights


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



def test_curve_deposit_weights_are_side_local_like_sdk():
    weights = deposit_weights(-2, 3, 0, StrategyType.CURVE)
    assert {i: weights[i] for i in (-2, -1, 0)} == {-2: 1, -1: 2, 0: 3}
    assert {i: weights[i] for i in (1, 2, 3)} == {1: 3, 2: 2, 3: 1}


def test_bid_ask_deposit_weights_push_toward_outer_edges():
    weights = deposit_weights(-2, 3, 0, StrategyType.BID_ASK)
    assert {i: weights[i] for i in (-2, -1, 0)} == {-2: 3, -1: 2, 0: 1}
    assert {i: weights[i] for i in (1, 2, 3)} == {1: 1, 2: 2, 3: 3}


def test_favor_x_moves_active_bin_to_ask_side():
    curve = deposit_weights(-2, 2, 0, StrategyType.CURVE, favor_x_in_active_bin=True)
    assert curve[-2] == 1
    assert curve[-1] == 2
    assert curve[0] == 3
    assert curve[1] == 2
    assert curve[2] == 1
