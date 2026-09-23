import pytest

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.multi_pool_research import (
    PoolResearchInput,
    build_multi_pool_research,
)
from meteora_learner.pool_safety import PoolSafetyConfig
from meteora_learner.capital_sizing import CapitalSizingConfig
from meteora_learner.storage import Storage
from meteora_learner.strategy import StrategyType


def save_pool(storage, address, fee_growth):
    storage.save_pool_snapshot(
        {
            "address": address,
            "name": address,
            "tvl": 100000,
            "volume_24h": 20000,
            "fees_24h": 100,
            "current_price": 1.0,
            "bin_step": 25,
            "active_bin_id": 0,
            "token_x": {"symbol": "X", "decimals": 6},
            "token_y": {"symbol": "Y", "decimals": 6},
            "dynamic_fee_pct": 0.5,
            "is_blacklisted": False,
            "created_at": 1790035200,
        },
        observed_at="2026-09-23T00:05:00+00:00",
    )
    for observed, checkpoint in (
        ("2026-09-23T00:00:00+00:00", 0),
        ("2026-09-23T00:05:00+00:00", fee_growth),
    ):
        storage.save_chain_pool_snapshot(
            {
                "pool_address": address,
                "active_bin_id": 0,
                "bin_step": 25,
                "token_x_mint": "x",
                "token_y_mint": "y",
                "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
                "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
                "base_fee_rate": "0",
                "variable_fee_rate": "0",
                "total_fee_rate": "0",
                "deposit_total_fee_rate": "0",
                "protocol_share_bps": 0,
                "collect_fee_mode": 0,
                "bin_arrays": [
                    {
                        "address": f"{address}-array",
                        "index": 0,
                        "lower_bin_id": 0,
                        "upper_bin_id": 0,
                        "bins": [
                            {
                                "bin_id": 0,
                                "price": str(Q64),
                                "amount_x": "1000",
                                "amount_y": "1000",
                                "liquidity_supply": str(2000 * Q64),
                                "fee_amount_x_per_token_stored": "0",
                                "fee_amount_y_per_token_stored": str(checkpoint),
                            }
                        ],
                    }
                ],
            },
            observed_at=observed,
        )


def test_multi_pool_research_uses_equal_notional_and_normalized_economics(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(storage, "slow", Q64)
    save_pool(storage, "fast", 2 * Q64)

    report = build_multi_pool_research(
        str(storage.path),
        inputs=(
            PoolResearchInput("slow", 0, 10, 100.0, 0),
            PoolResearchInput("fast", 0, 10, 100.0, 0),
        ),
        account_equity_quote=1000,
        cash_quote=1000,
        current_deployed_quote=0,
        portfolio_drawdown_bps=0,
        phase2_gate=None,
        safety_config=PoolSafetyConfig(
            min_tvl_usd=0,
            min_volume_24h_usd=0,
            min_pool_age_hours=0,
            min_chain_observations=2,
            max_dynamic_fee_pct=1.0,
        ),
        sizing_config=CapitalSizingConfig(
            max_position_bps=1000,
            max_total_deployed_bps=7000,
            min_cash_reserve_bps=3000,
        ),
        observation_limit=2,
        half_widths=(0,),
        strategies=(StrategyType.SPOT,),
    )

    assert report.pools_requested == 2
    assert report.research_ready_pools == 2
    assert report.policy_authorized_pools == 0
    assert report.leader_pool_address == "fast"
    assert report.live_execution_ready is False


def test_multi_pool_research_rejects_mixed_requested_notional(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="same requested_quote"):
        build_multi_pool_research(
            str(storage.path),
            inputs=(
                PoolResearchInput("a", 0, 10, 100.0, 0),
                PoolResearchInput("b", 0, 10, 200.0, 0),
            ),
            account_equity_quote=1000,
            cash_quote=1000,
            current_deployed_quote=0,
            portfolio_drawdown_bps=0,
            phase2_gate=None,
        )


def test_multi_pool_research_uses_exact_per_pool_observation_windows(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    save_pool(storage, "slow", Q64)
    save_pool(storage, "fast", 2 * Q64)

    for pool in ("slow", "fast"):
        storage.save_chain_pool_snapshot(
            {
                "pool_address": pool,
                "active_bin_id": 0,
                "bin_step": 25,
                "token_x_mint": "x",
                "token_y_mint": "y",
                "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
                "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
                "base_fee_rate": "0",
                "variable_fee_rate": "0",
                "total_fee_rate": "0",
                "deposit_total_fee_rate": "0",
                "protocol_share_bps": 0,
                "collect_fee_mode": 0,
                "bin_arrays": [
                    {
                        "address": f"{pool}-array",
                        "index": 0,
                        "lower_bin_id": 0,
                        "upper_bin_id": 0,
                        "bins": [
                            {
                                "bin_id": 0,
                                "price": str(Q64),
                                "amount_x": "1000",
                                "amount_y": "1000",
                                "liquidity_supply": str(2000 * Q64),
                                "fee_amount_x_per_token_stored": "0",
                                "fee_amount_y_per_token_stored": str(10 * Q64),
                            }
                        ],
                    }
                ],
            },
            observed_at="2026-09-23T00:10:00+00:00",
        )

    windows = {
        pool: (
            "2026-09-23T00:00:00+00:00",
            "2026-09-23T00:05:00+00:00",
        )
        for pool in ("slow", "fast")
    }
    report = build_multi_pool_research(
        str(storage.path),
        inputs=(
            PoolResearchInput("slow", 0, 10, 100.0, 0),
            PoolResearchInput("fast", 0, 10, 100.0, 0),
        ),
        account_equity_quote=1000,
        cash_quote=1000,
        current_deployed_quote=0,
        portfolio_drawdown_bps=0,
        phase2_gate=None,
        safety_config=PoolSafetyConfig(
            min_tvl_usd=0,
            min_volume_24h_usd=0,
            min_pool_age_hours=0,
            min_chain_observations=2,
            max_dynamic_fee_pct=1.0,
        ),
        sizing_config=CapitalSizingConfig(
            max_position_bps=1000,
            max_total_deployed_bps=7000,
            min_cash_reserve_bps=3000,
        ),
        observation_times_by_pool=windows,
        half_widths=(0,),
        strategies=(StrategyType.SPOT,),
    )

    assert all(
        plan.decision_observed_at == "2026-09-23T00:05:00+00:00"
        for plan in report.plans
    )
    assert all(
        plan.scan is not None and plan.scan.observation_count == 2
        for plan in report.plans
    )


def test_multi_pool_research_rejects_unknown_observation_window_pool(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="unknown pools"):
        build_multi_pool_research(
            str(storage.path),
            inputs=(
                PoolResearchInput("a", 0, 10, 100.0, 0),
                PoolResearchInput("b", 0, 10, 100.0, 0),
            ),
            account_equity_quote=1000,
            cash_quote=1000,
            current_deployed_quote=0,
            portfolio_drawdown_bps=0,
            phase2_gate=None,
            observation_times_by_pool={
                "unknown": (
                    "2026-09-23T00:00:00+00:00",
                    "2026-09-23T00:05:00+00:00",
                )
            },
        )
