from meteora_learner.liquidity_math import Q64
from meteora_learner.reconciliation_corpus import build_reconciliation_corpus
from meteora_learner.storage import Storage


def save_position(
    storage,
    address,
    observed_at,
    *,
    checkpoint_x=0,
    checkpoint_y=0,
    position_fee_x=0,
    position_fee_y=0,
    position_x=100,
    position_y=200,
    share=10 * Q64,
    claimed_x="0",
):
    storage.save_chain_position_snapshot(
        {
            "position_address": address,
            "pool_address": "pool",
            "owner": "owner",
            "fee_owner": "owner",
            "lower_bin_id": 100,
            "upper_bin_id": 100,
            "total_x_amount": str(position_x),
            "total_y_amount": str(position_y),
            "fee_x": str(position_fee_x),
            "fee_y": str(position_fee_y),
            "reward_one": "0",
            "reward_two": "0",
            "last_updated_at": 1,
            "total_claimed_fee_x_amount": claimed_x,
            "total_claimed_fee_y_amount": "0",
            "bins": [
                {
                    "bin_id": 100,
                    "price": str(Q64),
                    "bin_x_amount": "1000",
                    "bin_y_amount": "2000",
                    "bin_liquidity": str(100 * Q64),
                    "bin_fee_x_per_token_stored": str(checkpoint_x),
                    "bin_fee_y_per_token_stored": str(checkpoint_y),
                    "position_liquidity": str(share),
                    "position_x_amount": str(position_x),
                    "position_y_amount": str(position_y),
                    "position_fee_x_amount": str(position_fee_x),
                    "position_fee_y_amount": str(position_fee_y),
                    "position_reward_amounts": ["0", "0"],
                }
            ],
        },
        observed_at=observed_at,
    )


def test_corpus_gate_passes_only_with_exact_amount_and_fee_math(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(storage, "a", "2026-09-22T00:00:00+00:00")
    save_position(
        storage,
        "a",
        "2026-09-22T00:05:00+00:00",
        checkpoint_x=2 * Q64,
        checkpoint_y=3 * Q64,
        position_fee_x=20,
        position_fee_y=30,
    )

    report = build_reconciliation_corpus(str(db))

    assert report.positions_seen == 1
    assert report.amount_positions_exact == 1
    assert report.fee_intervals_seen == 1
    assert report.fee_intervals_exact == 1
    assert report.amount_mismatched_bins == 0
    assert report.fee_mismatched_bins == 0
    assert report.amount_exact_rate == 1.0
    assert report.fee_exact_rate == 1.0
    assert report.strict_math_gate_passed is True


def test_corpus_preserves_ineligible_fee_interval_reason(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(storage, "a", "2026-09-22T00:00:00+00:00")
    save_position(
        storage,
        "a",
        "2026-09-22T00:05:00+00:00",
        checkpoint_x=Q64,
        position_fee_x=0,
        claimed_x="1",
    )

    report = build_reconciliation_corpus(str(db))

    assert report.amount_positions_exact == 1
    assert report.fee_positions_with_two_snapshots == 1
    assert report.fee_intervals_eligible == 0
    assert "claimed fees changed" in str(report.entries[0].fee_interval_error)
    assert report.strict_math_gate_passed is False


def test_corpus_gate_fails_on_amount_mismatch(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(
        storage,
        "a",
        "2026-09-22T00:00:00+00:00",
        position_x=99,
    )

    report = build_reconciliation_corpus(str(db))

    assert report.amount_positions_eligible == 1
    assert report.amount_positions_exact == 0
    assert report.amount_mismatched_bins == 1
    assert report.strict_math_gate_passed is False


def test_corpus_limit_uses_latest_positions(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(storage, "old", "2026-09-22T00:00:00+00:00")
    save_position(storage, "new", "2026-09-22T01:00:00+00:00")

    report = build_reconciliation_corpus(str(db), position_limit=1)

    assert report.positions_seen == 1
    assert report.entries[0].position_address == "new"



def test_corpus_validates_all_consecutive_fee_intervals(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(storage, "a", "2026-09-22T00:00:00+00:00")
    save_position(
        storage,
        "a",
        "2026-09-22T00:05:00+00:00",
        checkpoint_x=Q64,
        position_fee_x=10,
    )
    save_position(
        storage,
        "a",
        "2026-09-22T00:10:00+00:00",
        checkpoint_x=3 * Q64,
        position_fee_x=30,
    )

    report = build_reconciliation_corpus(str(db))

    assert report.fee_intervals_seen == 2
    assert report.fee_intervals_eligible == 2
    assert report.fee_intervals_exact == 2
    assert report.fee_exact_rate == 1.0
    assert report.strict_math_gate_passed is True
