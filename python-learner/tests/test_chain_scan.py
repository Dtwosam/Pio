from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.chain_scan import scan_chain_candidates
from meteora_learner.liquidity_math import Q64
from meteora_learner.storage import Storage
from meteora_learner.strategy import StrategyType


def save_grid_snapshot(storage, observed_at):
    bins = []
    for bin_id in (-1, 0, 1):
        bins.append(
            {
                "bin_id": bin_id,
                "price": str(Q64),
                "amount_x": "100",
                "amount_y": "100",
                "liquidity_supply": str(200 * Q64),
                "fee_amount_x_per_token_stored": "0",
                "fee_amount_y_per_token_stored": "0",
            }
        )

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
                    "lower_bin_id": -1,
                    "upper_bin_id": 1,
                    "bins": bins,
                }
            ],
        },
        observed_at=observed_at,
    )


def test_chain_scan_keeps_accepted_candidates_and_raw_replay(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_grid_snapshot(storage, "2026-09-22T00:00:00+00:00")
    save_grid_snapshot(storage, "2026-09-22T00:05:00+00:00")

    result = scan_chain_candidates(
        str(db),
        pool_address="pool",
        amount_x=2,
        amount_y=2,
        observation_limit=2,
        half_widths=(0, 1),
        strategies=(StrategyType.SPOT, StrategyType.CURVE),
        max_share_bps=500,
    )

    assert result.attempted == 4
    assert result.accepted == 4
    assert result.rejected == 0
    assert all(item.status == "ACCEPTED" for item in result.candidates)
    assert all(item.range_survival_ratio == 1.0 for item in result.candidates)
    assert all(item.replay is not None for item in result.candidates)


def test_chain_scan_preserves_rejection_reason_for_uncovered_range(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_grid_snapshot(storage, "2026-09-22T00:00:00+00:00")
    save_grid_snapshot(storage, "2026-09-22T00:05:00+00:00")

    result = scan_chain_candidates(
        str(db),
        pool_address="pool",
        amount_x=1,
        amount_y=1,
        observation_limit=2,
        half_widths=(2,),
        strategies=(StrategyType.SPOT,),
    )

    assert result.attempted == 1
    assert result.accepted == 0
    assert result.rejected == 1
    candidate = result.candidates[0]
    assert candidate.status == "REJECTED"
    assert candidate.replay is None
    assert "bin 2" in str(candidate.rejection_reason)


def test_chain_scan_deduplicates_same_range_strategy(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_grid_snapshot(storage, "2026-09-22T00:00:00+00:00")
    save_grid_snapshot(storage, "2026-09-22T00:05:00+00:00")

    result = scan_chain_candidates(
        str(db),
        pool_address="pool",
        amount_x=1,
        amount_y=1,
        observation_limit=2,
        half_widths=(1, 1),
        center_offsets=(0, 0),
        strategies=(StrategyType.SPOT,),
    )

    assert result.attempted == 1
