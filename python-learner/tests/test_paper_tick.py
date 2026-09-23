from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import create_paper_account, open_paper_position
from meteora_learner.paper_chain import bind_paper_position_to_chain
from meteora_learner.paper_tick import run_paper_tick
from meteora_learner.pool_safety import PoolSafetyConfig
from meteora_learner.position_policy import PositionManagementConfig
from meteora_learner.quote_registry import save_token_quote
from meteora_learner.storage import Storage


TICK = "2026-09-23T10:00:00+00:00"
ENTRY = "2026-09-23T09:55:00+00:00"


def market_payload(address):
    return {
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
    }


def chain_payload(address):
    return {
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
                        "fee_amount_y_per_token_stored": str(Q64),
                        "reward_per_token_stored": ["0", "0"],
                    }
                ],
            }
        ],
    }


def safety():
    return PoolSafetyConfig(
        min_tvl_usd=0,
        min_volume_24h_usd=0,
        min_pool_age_hours=0,
        min_chain_observations=2,
        max_dynamic_fee_pct=1.0,
        max_pool_snapshot_age_seconds=300,
    )


def seed(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    # Entry chain state is old enough to require one refresh during the tick.
    first = chain_payload("pool")
    first["bin_arrays"][0]["bins"][0]["fee_amount_y_per_token_stored"] = "0"
    storage.save_chain_pool_snapshot(first, observed_at=ENTRY)

    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    open_paper_position(
        storage,
        event_key="enter",
        account_id="paper",
        position_id="pos",
        pool_address="pool",
        policy_source="DETERMINISTIC",
        strategy="SPOT",
        min_bin_id=0,
        max_bin_id=0,
        capital_quote=100,
    )
    bind_paper_position_to_chain(
        storage,
        position_id="pos",
        observed_at=ENTRY,
        amount_x=0,
        amount_y=100,
        token_y_quote_per_atomic=1.0,
    )
    save_token_quote(
        storage,
        token_mint="y",
        quote_per_atomic=1.0,
        source="TEST",
        observed_at=TICK,
    )
    return storage


def test_paper_tick_refreshes_market_chain_and_runs_supervisor(tmp_path):
    storage = seed(tmp_path)
    market_calls = []
    chain_calls = []

    def fetch_market(address):
        market_calls.append(address)
        return market_payload(address)

    def inspect_chain(address, radius):
        chain_calls.append((address, radius))
        return chain_payload(address)

    result = run_paper_tick(
        storage,
        account_id="paper",
        tick_id="tick-1",
        observed_at=TICK,
        chain_max_age_seconds=60,
        quote_max_age_seconds=60,
        array_radius=1,
        safety_config=safety(),
        management_config=PositionManagementConfig(stop_loss_bps=5000),
        fetch_pool=fetch_market,
        inspect_pool=inspect_chain,
    )

    assert result.status == "COMPLETE"
    assert market_calls == ["pool"]
    assert chain_calls == [("pool", 1)]
    assert result.market["pools_refreshed"] == 1
    assert result.chain["pools_refreshed"] == 1
    assert result.supervisor["portfolio"]["scheduled_positions"] == 1


def test_paper_tick_same_id_is_idempotent(tmp_path):
    storage = seed(tmp_path)
    counts = {"market": 0, "chain": 0}

    def fetch_market(address):
        counts["market"] += 1
        return market_payload(address)

    def inspect_chain(address, radius):
        counts["chain"] += 1
        return chain_payload(address)

    first = run_paper_tick(
        storage,
        account_id="paper",
        tick_id="tick-1",
        observed_at=TICK,
        chain_max_age_seconds=60,
        quote_max_age_seconds=60,
        safety_config=safety(),
        fetch_pool=fetch_market,
        inspect_pool=inspect_chain,
    )
    second = run_paper_tick(
        storage,
        account_id="paper",
        tick_id="tick-1",
        observed_at=TICK,
        chain_max_age_seconds=60,
        quote_max_age_seconds=60,
        safety_config=safety(),
        fetch_pool=fetch_market,
        inspect_pool=inspect_chain,
    )

    assert first.reused_existing_tick is False
    assert second.reused_existing_tick is True
    assert counts == {"market": 1, "chain": 1}


def test_paper_tick_fails_closed_when_market_refresh_fails(tmp_path):
    storage = seed(tmp_path)
    chain_calls = []

    def bad_market(address):
        raise RuntimeError("market api unavailable")

    def inspect_chain(address, radius):
        chain_calls.append(address)
        return chain_payload(address)

    result = run_paper_tick(
        storage,
        account_id="paper",
        tick_id="tick-fail",
        observed_at=TICK,
        chain_max_age_seconds=60,
        quote_max_age_seconds=60,
        safety_config=safety(),
        fetch_pool=bad_market,
        inspect_pool=inspect_chain,
    )

    assert result.status == "MARKET_REFRESH_FAILED"
    assert result.supervisor is None
    assert result.chain is None
    assert chain_calls == []
