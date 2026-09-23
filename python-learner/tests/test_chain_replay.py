from meteora_learner.chain_replay import (
    STANDARD_SPL_TOKEN_PROGRAM,
    replay_latest_small_lp_interval,
    replay_small_lp_history,
)
from meteora_learner.liquidity_math import Q64
from meteora_learner.storage import Storage
from meteora_learner.strategy import StrategyType


def save_snapshot(storage, observed_at, *, y_amount, fee_y_per_token, active=0):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool",
            "active_bin_id": active,
            "bin_step": 25,
            "token_x_mint": "x",
            "token_y_mint": "y",
            "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
            "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
            "base_fee_rate": "1000",
            "variable_fee_rate": "0",
            "total_fee_rate": "1000",
            "deposit_total_fee_rate": "1000",
            "protocol_share_bps": 0,
            "collect_fee_mode": 0,
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
                            "amount_x": "0",
                            "amount_y": str(y_amount),
                            "liquidity_supply": str(100 * Q64),
                            "fee_amount_x_per_token_stored": "0",
                            "fee_amount_y_per_token_stored": str(fee_y_per_token),
                        }
                    ],
                }
            ],
        },
        observed_at=observed_at,
    )


def test_small_lp_replay_marks_inventory_and_diluted_fees(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_snapshot(storage, "2026-09-22T00:00:00+00:00", y_amount=100, fee_y_per_token=0)
    save_snapshot(
        storage,
        "2026-09-22T00:05:00+00:00",
        y_amount=110,
        fee_y_per_token=100 * Q64,
    )

    result = replay_latest_small_lp_interval(
        str(db),
        pool_address="pool",
        amount_x=0,
        amount_y=1,
        min_bin_id=0,
        max_bin_id=0,
        strategy=StrategyType.SPOT,
        max_share_bps=200,
    )

    assert result.deposited_y == 1
    assert result.ending_y == 1
    assert result.bins[0].share_bps_of_start_supply == 100
    assert result.fee_y == 99
    assert result.replay_fidelity == "SMALL_LP_CHAIN_PATH_V2"
    assert result.observation_count == 2
    assert len(result.intervals) == 1
    assert result.reward_one == 0
    assert result.reward_two == 0
    assert result.reward_fidelity == "UNAVAILABLE_LEGACY_SNAPSHOT"


def test_replay_rejects_position_large_enough_to_distort_history(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_snapshot(storage, "2026-09-22T00:00:00+00:00", y_amount=100, fee_y_per_token=0)
    save_snapshot(storage, "2026-09-22T00:05:00+00:00", y_amount=100, fee_y_per_token=0)

    try:
        replay_latest_small_lp_interval(
            str(db),
            pool_address="pool",
            amount_x=0,
            amount_y=10,
            min_bin_id=0,
            max_bin_id=0,
            strategy=StrategyType.SPOT,
            max_share_bps=500,
        )
    except ValueError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("expected small-LP guard to reject oversized replay")



def test_replay_rejects_token_2022_until_transfer_fee_model_exists(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    observed = "2026-09-22T00:00:00+00:00"
    for offset in (0, 5):
        payload = {
            "pool_address": "pool",
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": "x",
            "token_y_mint": "y",
            "token_x_program": "Token2022Unsupported",
            "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
            "base_fee_rate": "1000",
            "variable_fee_rate": "0",
            "total_fee_rate": "1000",
            "deposit_total_fee_rate": "1000",
            "protocol_share_bps": 0,
            "collect_fee_mode": 0,
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
                            "amount_x": "0",
                            "amount_y": "100",
                            "liquidity_supply": str(100 * Q64),
                            "fee_amount_x_per_token_stored": "0",
                            "fee_amount_y_per_token_stored": "0",
                        }
                    ],
                }
            ],
        }
        storage.save_chain_pool_snapshot(
            payload,
            observed_at=f"2026-09-22T00:{offset:02d}:00+00:00",
        )

    try:
        replay_latest_small_lp_interval(
            str(db),
            pool_address="pool",
            amount_x=0,
            amount_y=1,
            min_bin_id=0,
            max_bin_id=0,
            strategy=StrategyType.SPOT,
        )
    except ValueError as exc:
        assert "Token-2022" in str(exc)
    else:
        raise AssertionError("expected Token-2022 replay to fail closed")



def test_replay_reports_active_bin_composition_fee_separately(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)

    for minute in (0, 5):
        storage.save_chain_pool_snapshot(
            {
                "pool_address": "pool",
                "active_bin_id": 0,
                "bin_step": 25,
                "token_x_mint": "x",
                "token_y_mint": "y",
                "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
                "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
                "base_fee_rate": "10000000",
                "variable_fee_rate": "0",
                "total_fee_rate": "10000000",
                "deposit_total_fee_rate": "10000000",
                "protocol_share_bps": 1000,
                "collect_fee_mode": 0,
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
                                "amount_x": "100000",
                                "amount_y": "100000",
                                "liquidity_supply": str(200000 * Q64),
                                "fee_amount_x_per_token_stored": "0",
                                "fee_amount_y_per_token_stored": "0",
                            }
                        ],
                    }
                ],
            },
            observed_at=f"2026-09-22T00:{minute:02d}:00+00:00",
        )

    result = replay_latest_small_lp_interval(
        str(db),
        pool_address="pool",
        amount_x=0,
        amount_y=1000,
        min_bin_id=0,
        max_bin_id=0,
        strategy=StrategyType.SPOT,
        max_share_bps=100,
    )

    assert result.entry_composition_fee_x == 0
    assert result.entry_composition_fee_y > 0
    assert result.entry_composition_protocol_fee_y == result.entry_composition_fee_y // 10
    assert result.fee_x == 0
    assert result.fee_y == 0



def test_multi_snapshot_replay_recalculates_dilution_each_interval(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)

    def save(minute, supply, y_amount, fee_checkpoint):
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
                                "amount_x": "0",
                                "amount_y": str(y_amount),
                                "liquidity_supply": str(supply * Q64),
                                "fee_amount_x_per_token_stored": "0",
                                "fee_amount_y_per_token_stored": str(fee_checkpoint * Q64),
                            }
                        ],
                    }
                ],
            },
            observed_at=f"2026-09-22T00:{minute:02d}:00+00:00",
        )

    save(0, 100, 100, 0)
    save(5, 200, 200, 1000)
    save(10, 200, 200, 2000)

    result = replay_small_lp_history(
        str(db),
        pool_address="pool",
        amount_x=0,
        amount_y=1,
        min_bin_id=0,
        max_bin_id=0,
        strategy=StrategyType.SPOT,
        observation_limit=3,
        max_share_bps=200,
    )

    assert result.observation_count == 3
    assert len(result.intervals) == 2
    assert result.intervals[0].fee_y == 990
    assert result.intervals[1].fee_y == 995
    assert result.fee_y == 1985
    assert result.max_observed_share_bps == 100


def test_multi_snapshot_replay_rejects_later_supply_shrink(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)

    def save(minute, supply):
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
                                "amount_x": "0",
                                "amount_y": str(supply),
                                "liquidity_supply": str(supply * Q64),
                                "fee_amount_x_per_token_stored": "0",
                                "fee_amount_y_per_token_stored": "0",
                            }
                        ],
                    }
                ],
            },
            observed_at=f"2026-09-22T00:{minute:02d}:00+00:00",
        )

    save(0, 100)
    save(5, 25)
    save(10, 25)

    try:
        replay_small_lp_history(
            str(db),
            pool_address="pool",
            amount_x=0,
            amount_y=1,
            min_bin_id=0,
            max_bin_id=0,
            strategy=StrategyType.SPOT,
            observation_limit=3,
            max_share_bps=200,
        )
    except ValueError as exc:
        assert "too large" in str(exc)
        assert "400 bps" in str(exc)
    else:
        raise AssertionError("expected later small-LP guard to reject replay")


def test_multi_snapshot_replay_rejects_historical_zero_supply(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)

    for minute, supply in ((0, 100), (5, 0)):
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
                                "amount_x": "0",
                                "amount_y": str(supply),
                                "liquidity_supply": str(supply * Q64),
                                "fee_amount_x_per_token_stored": "0",
                                "fee_amount_y_per_token_stored": "0",
                            }
                        ],
                    }
                ],
            },
            observed_at=f"2026-09-22T00:{minute:02d}:00+00:00",
        )

    try:
        replay_small_lp_history(
            str(db),
            pool_address="pool",
            amount_x=0,
            amount_y=1,
            min_bin_id=0,
            max_bin_id=0,
            strategy=StrategyType.SPOT,
            observation_limit=2,
        )
    except ValueError as exc:
        assert "zero supply" in str(exc)
    else:
        raise AssertionError("expected zero-supply path to fail closed")



def save_reward_snapshot(
    storage,
    observed_at,
    *,
    reward_checkpoint,
    reward_mint="reward-mint",
    supports_limit_order=False,
):
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
            "supports_limit_order": supports_limit_order,
            "reward_mints": [
                reward_mint,
                "11111111111111111111111111111111",
            ],
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
                            "amount_x": "0",
                            "amount_y": "100",
                            "liquidity_supply": str(100 * Q64),
                            "fee_amount_x_per_token_stored": "0",
                            "fee_amount_y_per_token_stored": "0",
                            "reward_per_token_stored": [
                                str(reward_checkpoint),
                                "0",
                            ],
                        }
                    ],
                }
            ],
        },
        observed_at=observed_at,
    )


def test_chain_replay_attributes_reward_checkpoint_growth(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_reward_snapshot(
        storage,
        "2026-09-22T00:00:00+00:00",
        reward_checkpoint=0,
    )
    save_reward_snapshot(
        storage,
        "2026-09-22T00:05:00+00:00",
        reward_checkpoint=100 * Q64,
    )

    result = replay_latest_small_lp_interval(
        str(db),
        pool_address="pool",
        amount_x=0,
        amount_y=1,
        min_bin_id=0,
        max_bin_id=0,
        strategy=StrategyType.SPOT,
        max_share_bps=200,
    )

    assert result.reward_one == 99
    assert result.reward_two == 0
    assert result.intervals[0].reward_one == 99
    assert result.bins[0].reward_one == 99
    assert result.reward_mint_0 == "reward-mint"
    assert result.reward_fidelity == "ONCHAIN_EFFECTIVE_REWARD_CHECKPOINT_V1"


def test_chain_replay_rejects_reward_campaign_change(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_reward_snapshot(
        storage,
        "2026-09-22T00:00:00+00:00",
        reward_checkpoint=0,
        reward_mint="reward-a",
    )
    save_reward_snapshot(
        storage,
        "2026-09-22T00:05:00+00:00",
        reward_checkpoint=Q64,
        reward_mint="reward-b",
    )

    try:
        replay_latest_small_lp_interval(
            str(db),
            pool_address="pool",
            amount_x=0,
            amount_y=1,
            min_bin_id=0,
            max_bin_id=0,
            strategy=StrategyType.SPOT,
        )
    except ValueError as exc:
        assert "reward mint changed" in str(exc)
    else:
        raise AssertionError("expected reward campaign change to fail closed")


def test_chain_replay_marks_rewards_not_applicable_for_limit_order_pool(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_reward_snapshot(
        storage,
        "2026-09-22T00:00:00+00:00",
        reward_checkpoint=0,
        supports_limit_order=True,
    )
    save_reward_snapshot(
        storage,
        "2026-09-22T00:05:00+00:00",
        reward_checkpoint=0,
        supports_limit_order=True,
    )

    result = replay_latest_small_lp_interval(
        str(db),
        pool_address="pool",
        amount_x=0,
        amount_y=1,
        min_bin_id=0,
        max_bin_id=0,
        strategy=StrategyType.SPOT,
        max_share_bps=200,
    )

    assert result.reward_one == 0
    assert result.reward_two == 0
    assert result.reward_fidelity == "NOT_APPLICABLE_LIMIT_ORDER_POOL"
