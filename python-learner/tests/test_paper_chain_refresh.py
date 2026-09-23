from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import create_paper_account, open_paper_position
from meteora_learner.paper_chain_refresh import (
    inspect_pool_with_rust,
    refresh_paper_chain_state,
)
from meteora_learner.storage import Storage


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


def payload(pool):
    return {
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
    }


def test_refresh_deduplicates_pools_and_ingests_rust_payload(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    open_position(storage, "a", "pool")
    open_position(storage, "b", "pool")
    calls = []

    def inspect(pool, radius):
        calls.append((pool, radius))
        return payload(pool)

    report = refresh_paper_chain_state(
        storage,
        account_id="paper",
        max_age_seconds=300,
        array_radius=2,
        as_of="2026-09-23T10:00:00+00:00",
        inspector=inspect,
    )

    assert calls == [("pool", 2)]
    assert report.pools_attempted == 1
    assert report.pools_refreshed == 1
    assert report.pools_failed == 0
    assert report.items[0].status == "REFRESHED"

    with storage.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM chain_pool_snapshots WHERE pool_address = 'pool'"
        ).fetchone()[0]
    assert count == 1


def test_refresh_isolates_pool_failure(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    open_position(storage, "a", "bad")
    open_position(storage, "b", "good")

    def inspect(pool, radius):
        if pool == "bad":
            raise RuntimeError("rpc unavailable")
        return payload(pool)

    report = refresh_paper_chain_state(
        storage,
        account_id="paper",
        as_of="2026-09-23T10:00:00+00:00",
        inspector=inspect,
    )

    assert report.pools_attempted == 2
    assert report.pools_refreshed == 1
    assert report.pools_failed == 1
    by_pool = {item.pool_address: item for item in report.items}
    assert by_pool["bad"].status == "FAILED"
    assert "rpc unavailable" in by_pool["bad"].error
    assert by_pool["good"].status == "REFRESHED"



def test_rust_inspector_accepts_solana_rpc_url_env(tmp_path, monkeypatch):
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("[package]\nname='test'\nversion='0.1.0'\n")
    monkeypatch.setenv("SOLANA_RPC_URL", "https://rpc.example")
    monkeypatch.delenv("RPC_URL", raising=False)

    calls = []

    class Result:
        returncode = 0
        stdout = '{"pool_address":"pool"}'
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return Result()

    monkeypatch.setattr(
        "meteora_learner.paper_chain_refresh.subprocess.run",
        fake_run,
    )

    result = inspect_pool_with_rust(
        "pool",
        1,
        rust_manifest_path=manifest,
    )

    assert result["pool_address"] == "pool"
    assert "inspect-pool-env" in calls[0][0]
