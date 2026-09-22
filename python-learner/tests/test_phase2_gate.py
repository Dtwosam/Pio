from meteora_learner.liquidity_math import Q64
from meteora_learner.phase2_gate import (
    Phase2PromotionCriteria,
    evaluate_phase2_promotion_gate,
)
from meteora_learner.storage import Storage


def save_position(storage, address, observed_at, checkpoint=0, fee=0):
    storage.save_chain_position_snapshot(
        {
            "position_address": address,
            "pool_address": "pool",
            "owner": "owner",
            "fee_owner": "owner",
            "lower_bin_id": 1,
            "upper_bin_id": 1,
            "total_x_amount": "100",
            "total_y_amount": "200",
            "fee_x": str(fee),
            "fee_y": "0",
            "reward_one": "0",
            "reward_two": "0",
            "last_updated_at": 1,
            "total_claimed_fee_x_amount": "0",
            "total_claimed_fee_y_amount": "0",
            "bins": [
                {
                    "bin_id": 1,
                    "price": str(Q64),
                    "bin_x_amount": "1000",
                    "bin_y_amount": "2000",
                    "bin_liquidity": str(100 * Q64),
                    "bin_fee_x_per_token_stored": str(checkpoint),
                    "bin_fee_y_per_token_stored": "0",
                    "position_liquidity": str(10 * Q64),
                    "position_x_amount": "100",
                    "position_y_amount": "200",
                    "position_fee_x_amount": str(fee),
                    "position_fee_y_amount": "0",
                    "position_reward_amounts": ["0", "0"],
                }
            ],
        },
        observed_at=observed_at,
    )


def test_phase2_gate_passes_only_when_math_and_sample_criteria_pass(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    for address in ("a", "b"):
        save_position(storage, address, "2026-09-22T00:00:00+00:00")
        save_position(
            storage,
            address,
            "2026-09-22T00:05:00+00:00",
            checkpoint=Q64,
            fee=10,
        )

    gate = evaluate_phase2_promotion_gate(
        str(db),
        criteria=Phase2PromotionCriteria(
            min_positions=2,
            min_amount_bins=2,
            min_fee_intervals=2,
            min_fee_bins=2,
        ),
    )

    assert gate.exact_math_passed is True
    assert gate.sample_sufficiency_passed is True
    assert gate.promotion_ready is True
    assert gate.reasons == ()


def test_phase2_gate_separates_math_from_sample_shortfall(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(storage, "a", "2026-09-22T00:00:00+00:00")
    save_position(
        storage,
        "a",
        "2026-09-22T00:05:00+00:00",
        checkpoint=Q64,
        fee=10,
    )

    gate = evaluate_phase2_promotion_gate(
        str(db),
        criteria=Phase2PromotionCriteria(
            min_positions=10,
            min_amount_bins=10,
            min_fee_intervals=10,
            min_fee_bins=10,
        ),
    )

    assert gate.exact_math_passed is True
    assert gate.sample_sufficiency_passed is False
    assert gate.promotion_ready is False
    assert any("positions_seen" in reason for reason in gate.reasons)


def test_phase2_gate_detects_exact_math_failure(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(storage, "a", "2026-09-22T00:00:00+00:00")
    save_position(
        storage,
        "a",
        "2026-09-22T00:05:00+00:00",
        checkpoint=Q64,
        fee=9,
    )

    gate = evaluate_phase2_promotion_gate(
        str(db),
        criteria=Phase2PromotionCriteria(
            min_positions=1,
            min_amount_bins=1,
            min_fee_intervals=1,
            min_fee_bins=1,
        ),
    )

    assert gate.exact_math_passed is False
    assert gate.sample_sufficiency_passed is True
    assert gate.promotion_ready is False
    assert any("fee bins" in reason for reason in gate.reasons)


def test_phase2_gate_validates_criteria():
    try:
        Phase2PromotionCriteria(
            min_positions=0,
            min_amount_bins=1,
            min_fee_intervals=1,
            min_fee_bins=1,
        )
    except ValueError as exc:
        assert "min_positions" in str(exc)
    else:
        raise AssertionError("expected invalid criteria to be rejected")
