from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import create_paper_account, open_paper_position
from meteora_learner.paper_chain_collection import build_paper_chain_collection_queue
from meteora_learner.storage import Storage


def save_chain(storage, pool, observed_at):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": pool,
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": f"{pool}-x",
            "token_y_mint": f"{pool}-y",
            "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
            "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
            "base_fee_rate": "0",
            "variable_fee_rate": "0",
            "total_fee_rate": "0",
            "deposit_total_fee_rate": "0",
            "protocol_share_bps": 0,
            "collect_fee_mode": 0,
            "supports_limit_order": False,
            "reward_mints": [f"{pool}-x", f"{pool}-y"],
            "reward_rates": ["0", "0"],
            "reward_duration_ends": [0, 0],
            "reward_last_update_times": [0, 0],
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
                            "fee_amount_y_per_token_stored": "0",
                            "reward_per_token_stored": ["0", "0"],
                        }
                    ],
                }
            ],
        },
        observed_at=observed_at,
    )


def open_position(storage, position, pool):
    open_paper_position(
        storage,
        event_key=f"{position}-enter",
        account_id="paper",
        position_id=position,
        pool_address=pool,
        policy_source="DETERMINISTIC",
        strategy="SPOT",
        min_bin_id=0,
        max_bin_id=0,
        capital_quote=100,
    )


def test_collection_queue_deduplicates_open_position_pools(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    open_position(storage, "a", "pool")
    open_position(storage, "b", "pool")

    queue = build_paper_chain_collection_queue(
        storage,
        account_id="paper",
        max_age_seconds=300,
        array_radius=2,
        as_of="2026-09-23T10:00:00+00:00",
    )

    assert queue.open_positions_seen == 2
    assert queue.pools_seen == 1
    assert queue.pools_needing_collection == 1
    item = queue.items[0]
    assert item.open_positions == ("a", "b")
    assert item.latest_observed_at is None
    assert "inspect-pool" in item.shell_command
    assert '"$RPC_URL"' in item.shell_command
    assert "pool 2" in item.shell_command
    assert "ingest-chain-snapshot" in item.shell_command


def test_collection_queue_only_emits_command_for_stale_pool(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    open_position(storage, "a", "fresh")
    open_position(storage, "b", "stale")
    save_chain(storage, "fresh", "2026-09-23T09:59:00+00:00")
    save_chain(storage, "stale", "2026-09-23T09:40:00+00:00")

    queue = build_paper_chain_collection_queue(
        storage,
        account_id="paper",
        max_age_seconds=300,
        as_of="2026-09-23T10:00:00+00:00",
    )

    by_pool = {item.pool_address: item for item in queue.items}
    assert by_pool["fresh"].needs_collection is False
    assert by_pool["fresh"].shell_command is None
    assert by_pool["stale"].needs_collection is True
    assert by_pool["stale"].age_seconds == 1200
    assert by_pool["stale"].shell_command is not None


def test_collection_queue_can_scope_one_account(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    create_paper_account(storage, account_id="other", starting_cash_quote=1000)
    open_position(storage, "a", "pool-a")
    open_paper_position(
        storage,
        event_key="other-enter",
        account_id="other",
        position_id="other-pos",
        pool_address="pool-b",
        policy_source="DETERMINISTIC",
        strategy="SPOT",
        min_bin_id=0,
        max_bin_id=0,
        capital_quote=100,
    )

    queue = build_paper_chain_collection_queue(
        storage,
        account_id="paper",
        as_of="2026-09-23T10:00:00+00:00",
    )
    assert [item.pool_address for item in queue.items] == ["pool-a"]
