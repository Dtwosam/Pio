from datetime import datetime, timezone

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.pool_safety import PoolSafetyConfig, screen_pool_universe
from meteora_learner.storage import Storage


def save_api_pool(
    storage,
    address,
    *,
    tvl=100000,
    volume=20000,
    blacklisted=False,
    observed_at="2026-09-23T00:00:00+00:00",
):
    storage.save_pool_snapshot(
        {
            "address": address,
            "name": f"{address}-pool",
            "tvl": tvl,
            "volume_24h": volume,
            "fees_24h": 100,
            "current_price": 1.0,
            "bin_step": 25,
            "active_bin_id": 0,
            "token_x": {"symbol": "X", "decimals": 6},
            "token_y": {"symbol": "Y", "decimals": 6},
            "dynamic_fee_pct": 0.5,
            "is_blacklisted": blacklisted,
            "created_at": int(
                datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp()
            ),
        },
        observed_at=observed_at,
    )


def save_chain(storage, address, observed_at):
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
            "bin_arrays": [],
        },
        observed_at=observed_at,
    )


def test_pool_safety_accepts_only_fully_observed_safe_pool(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_api_pool(storage, "good")
    save_api_pool(storage, "thin", tvl=1000)
    save_api_pool(storage, "blocked", blacklisted=True)
    for address in ("good", "thin", "blocked"):
        save_chain(storage, address, "2026-09-23T00:00:00+00:00")
        save_chain(storage, address, "2026-09-23T00:05:00+00:00")

    report = screen_pool_universe(
        str(storage.path),
        config=PoolSafetyConfig(
            min_tvl_usd=50000,
            min_volume_24h_usd=10000,
            min_pool_age_hours=24,
            min_chain_observations=2,
            max_dynamic_fee_pct=1.0,
        ),
        as_of="2026-09-23T00:00:00+00:00",
    )

    assert report.pools_seen == 3
    assert report.pools_accepted == 1
    accepted = [item for item in report.assessments if item.accepted]
    assert accepted[0].pool_address == "good"

    thin = next(item for item in report.assessments if item.pool_address == "thin")
    assert any("TVL" in reason for reason in thin.rejection_reasons)
    blocked = next(
        item for item in report.assessments if item.pool_address == "blocked"
    )
    assert "pool is blacklisted" in blocked.rejection_reasons


def test_pool_safety_fails_closed_when_chain_support_is_unknown(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_api_pool(storage, "api-only")

    report = screen_pool_universe(
        str(storage.path),
        config=PoolSafetyConfig(
            min_tvl_usd=0,
            min_volume_24h_usd=0,
            min_pool_age_hours=0,
            min_chain_observations=0,
            max_dynamic_fee_pct=None,
        ),
        as_of="2026-09-23T00:00:00+00:00",
    )

    item = report.assessments[0]
    assert item.accepted is False
    assert "token program support is unknown" in item.rejection_reasons



def test_pool_safety_rejects_stale_normalized_snapshot(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_api_pool(
        storage,
        "pool",
        blacklisted=False,
        observed_at="2026-09-23T09:00:00+00:00",
    )
    save_chain(storage, address="pool", observed_at="2026-09-23T09:10:00+00:00")

    report = screen_pool_universe(
        str(storage.path),
        config=PoolSafetyConfig(
            min_tvl_usd=0,
            min_volume_24h_usd=0,
            min_pool_age_hours=0,
            min_chain_observations=1,
            max_dynamic_fee_pct=10,
            max_pool_snapshot_age_seconds=300,
        ),
        as_of="2026-09-23T09:10:00+00:00",
    )

    assessment = report.assessments[0]
    assert assessment.accepted is False
    assert assessment.snapshot_age_seconds == 600
    assert any("snapshot age" in reason for reason in assessment.rejection_reasons)
