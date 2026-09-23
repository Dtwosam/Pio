import pytest

from meteora_learner.capital_sizing import (
    CapitalSizingConfig,
    size_position,
)


def test_sizing_respects_position_portfolio_and_reserve_caps():
    result = size_position(
        account_equity_quote=10_000,
        cash_quote=6_000,
        current_deployed_quote=4_000,
        portfolio_drawdown_bps=0,
        config=CapitalSizingConfig(
            max_position_bps=1_000,
            max_total_deployed_bps=7_000,
            min_cash_reserve_bps=3_000,
        ),
    )

    assert result.max_position_quote == 1000.0
    assert result.portfolio_room_quote == 3000.0
    assert result.reserve_room_quote == 3000.0
    assert result.sized_quote == 1000.0
    assert result.blocked is False


def test_sizing_throttles_after_soft_drawdown():
    result = size_position(
        account_equity_quote=10_000,
        cash_quote=10_000,
        current_deployed_quote=0,
        portfolio_drawdown_bps=600,
        config=CapitalSizingConfig(
            max_position_bps=2_000,
            max_total_deployed_bps=7_000,
            min_cash_reserve_bps=3_000,
            soft_drawdown_bps=500,
            hard_drawdown_bps=1_500,
            soft_drawdown_size_multiplier_bps=5_000,
        ),
    )

    assert result.max_position_quote == 2000.0
    assert result.drawdown_multiplier == 0.5
    assert result.sized_quote == 1000.0
    assert any("throttle" in reason for reason in result.reasons)


def test_sizing_blocks_at_hard_drawdown():
    result = size_position(
        account_equity_quote=10_000,
        cash_quote=10_000,
        current_deployed_quote=0,
        portfolio_drawdown_bps=1_500,
    )
    assert result.sized_quote == 0.0
    assert result.blocked is True
    assert any("hard limit" in reason for reason in result.reasons)


def test_sizing_never_exceeds_requested_amount():
    result = size_position(
        account_equity_quote=10_000,
        cash_quote=10_000,
        current_deployed_quote=0,
        portfolio_drawdown_bps=0,
        requested_quote=250,
    )
    assert result.sized_quote == 250.0


def test_invalid_combined_exposure_and_reserve_is_rejected():
    with pytest.raises(ValueError):
        CapitalSizingConfig(
            max_total_deployed_bps=8_000,
            min_cash_reserve_bps=3_000,
        )
