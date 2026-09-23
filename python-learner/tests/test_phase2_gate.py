from meteora_learner.calibration_status import Phase2CalibrationEvidence
from meteora_learner.liquidity_math import Q64
from meteora_learner.phase2_gate import (
    Phase2CapabilityStatus,
    Phase2PromotionCriteria,
    evaluate_phase2_promotion_gate,
)
from meteora_learner.storage import Storage


def save_position(storage, address, observed_at, checkpoint=0, fee=0):
    reward_one = (10 * int(checkpoint)) >> 64
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
            "reward_one": str(reward_one),
            "reward_two": "0",
            "supports_limit_order": False,
            "reward_mints": ["reward-mint", "11111111111111111111111111111111"],
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
                    "bin_reward_per_token_stored": [str(checkpoint), "0"],
                    "position_liquidity": str(10 * Q64),
                    "position_x_amount": "100",
                    "position_y_amount": "200",
                    "position_fee_x_amount": str(fee),
                    "position_fee_y_amount": "0",
                    "position_reward_amounts": [str(reward_one), "0"],
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
            min_reward_intervals=2,
            min_reward_growth_bins=2,
        ),
    )

    assert gate.exact_math_passed is True
    assert gate.sample_sufficiency_passed is True
    assert gate.capability_gate_passed is False
    assert gate.promotion_ready is False
    assert any("composition_formula_reconciliation" in reason for reason in gate.reasons)


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
            min_reward_intervals=10,
            min_reward_growth_bins=10,
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
            min_reward_intervals=1,
            min_reward_growth_bins=1,
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
            min_reward_intervals=1,
            min_reward_growth_bins=1,
        )
    except ValueError as exc:
        assert "min_positions" in str(exc)
    else:
        raise AssertionError("expected invalid criteria to be rejected")



def test_phase2_gate_can_pass_when_required_capabilities_are_validated(tmp_path):
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
            min_positions=1,
            min_amount_bins=1,
            min_fee_intervals=1,
            min_fee_bins=1,
            min_reward_intervals=1,
            min_reward_growth_bins=1,
        ),
        capabilities=Phase2CapabilityStatus(
            position_amount_reconciliation=True,
            fee_checkpoint_reconciliation=True,
            composition_event_labels=True,
            composition_formula_reconciliation=True,
            rebalance_lifecycle=True,
            reward_accounting=True,
            transaction_fee_calibration=True,
            add_execution_calibration=True,
            slippage_calibration=True,
        ),
    )

    assert gate.exact_math_passed is True
    assert gate.sample_sufficiency_passed is True
    assert gate.capability_gate_passed is True
    assert gate.promotion_ready is True



def test_phase2_gate_promotes_calibration_capabilities_from_real_evidence(
    tmp_path,
    monkeypatch,
):
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

    evidence = Phase2CalibrationEvidence(
        add_positions=1,
        composition_add_events=1,
        composition_eligible_samples=1,
        composition_exact_samples=1,
        composition_mismatched_samples=0,
        add_execution_events=1,
        add_execution_request_decodes=1,
        add_execution_matched_events=1,
        add_active_guard_samples=1,
        add_active_guard_violations=0,
        rebalance_positions=1,
        rebalance_events=1,
        rebalance_request_decodes=1,
        rebalance_guard_samples=1,
        rebalance_guard_violations=0,
        transaction_receipt_samples=2,
        transaction_fee_samples=2,
        missing_transaction_receipts=0,
        evidence_gaps=(),
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_gate.build_phase2_calibration_evidence",
        lambda _: evidence,
    )

    gate = evaluate_phase2_promotion_gate(
        str(db),
        criteria=Phase2PromotionCriteria(
            min_positions=1,
            min_amount_bins=1,
            min_fee_intervals=1,
            min_fee_bins=1,
            min_reward_intervals=1,
            min_reward_growth_bins=1,
            min_composition_samples=1,
            min_add_execution_samples=1,
            min_rebalance_guard_samples=1,
            min_transaction_fee_samples=1,
        ),
    )

    assert gate.sample_sufficiency_passed is True
    assert gate.capability_gate_passed is True
    assert gate.effective_capabilities.composition_formula_reconciliation is True
    assert gate.effective_capabilities.transaction_fee_calibration is True
    assert gate.effective_capabilities.add_execution_calibration is True
    assert gate.effective_capabilities.slippage_calibration is True
    assert gate.promotion_ready is True


def test_phase2_gate_rejects_negative_calibration_threshold():
    try:
        Phase2PromotionCriteria(
            min_positions=1,
            min_amount_bins=1,
            min_fee_intervals=1,
            min_fee_bins=1,
            min_reward_intervals=1,
            min_reward_growth_bins=1,
            min_composition_samples=-1,
        )
    except ValueError as exc:
        assert "min_composition_samples" in str(exc)
    else:
        raise AssertionError("expected negative calibration threshold to be rejected")
