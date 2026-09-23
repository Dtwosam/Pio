from types import SimpleNamespace

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import create_paper_account
from meteora_learner.paper_entry_workflow import (
    build_and_open_bound_phase3_paper_entry,
    paper_deployed_capital_quote,
)
from meteora_learner.phase_promotion import (
    persist_phase2_promotion,
    persist_phase3_promotion,
)
from meteora_learner.pool_safety import PoolSafetyConfig
from meteora_learner.storage import Storage
from meteora_learner.strategy import StrategyType


def save_pool(storage):
    storage.save_pool_snapshot(
        {
            "address": "pool",
            "name": "X-Y",
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
        observed_at="2026-09-23T09:05:00+00:00",
    )


def save_chain(storage, observed_at, fee_checkpoint=0):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool",
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
            "supports_limit_order": False,
            "reward_mints": ["x", "y"],
            "reward_rates": ["0", "0"],
            "reward_duration_ends": [0, 0],
            "reward_last_update_times": [0, 0],
            "bin_arrays": [
                {
                    "address": "array",
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
                            "fee_amount_y_per_token_stored": str(fee_checkpoint),
                            "reward_per_token_stored": ["0", "0"],
                        }
                    ],
                }
            ],
        },
        observed_at=observed_at,
    )


def promote(storage):
    ready = SimpleNamespace(
        promotion_ready=True,
        to_record=lambda: {"promotion_ready": True},
    )
    persist_phase2_promotion(storage, report=ready)
    persist_phase3_promotion(storage, report=ready)


def safety():
    return PoolSafetyConfig(
        min_tvl_usd=0,
        min_volume_24h_usd=0,
        min_pool_age_hours=0,
        min_chain_observations=2,
        max_dynamic_fee_pct=1.0,
    )


def test_workflow_derives_account_state_and_opens_bound_position(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(storage)
    save_chain(storage, "2026-09-23T09:00:00+00:00")
    save_chain(storage, "2026-09-23T09:05:00+00:00", Q64)
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    promote(storage)

    result = build_and_open_bound_phase3_paper_entry(
        storage,
        account_id="paper",
        position_id="pos",
        event_key="enter-pos",
        pool_address="pool",
        amount_x=0,
        amount_y=10,
        requested_quote=100,
        network_cost_y_atomic=0,
        safety_config=safety(),
        observation_limit=2,
        half_widths=(0,),
        strategies=(StrategyType.SPOT,),
    )

    assert result.current_deployed_quote == 0
    assert result.plan.policy_authorized is True
    assert result.entry.opened is True
    assert result.entry.bound is True
    assert paper_deployed_capital_quote(storage, account_id="paper") == 100


def test_workflow_blocks_without_persisted_phase3_promotion(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(storage)
    save_chain(storage, "2026-09-23T09:00:00+00:00")
    save_chain(storage, "2026-09-23T09:05:00+00:00", Q64)
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)

    ready = SimpleNamespace(
        promotion_ready=True,
        to_record=lambda: {"promotion_ready": True},
    )
    persist_phase2_promotion(storage, report=ready)

    result = build_and_open_bound_phase3_paper_entry(
        storage,
        account_id="paper",
        position_id="pos",
        event_key="enter-pos",
        pool_address="pool",
        amount_x=0,
        amount_y=10,
        requested_quote=100,
        network_cost_y_atomic=0,
        safety_config=safety(),
        observation_limit=2,
        half_widths=(0,),
        strategies=(StrategyType.SPOT,),
    )

    assert result.plan.policy_authorized is True
    assert result.entry.opened is False
    assert "Phase 3" in result.entry.reason
    assert paper_deployed_capital_quote(storage, account_id="paper") == 0
