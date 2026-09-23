import pytest

from meteora_learner.liquidity_math import Q64
from meteora_learner.reconciliation import (
    reconcile_latest_position_fee_interval,
    reconcile_latest_position_reward_interval,
    reconcile_position_fee_interval,
    reconcile_position_reward_interval,
    reconcile_position,
    reconcile_position_amounts,
)
from meteora_learner.storage import Storage


def save_position(
    storage,
    observed_at,
    *,
    checkpoint_x,
    checkpoint_y,
    fee_x,
    fee_y,
    claimed_x="0",
    claimed_y="0",
    share=10 * Q64,
    reward_checkpoint_one=None,
    reward_checkpoint_two=None,
    reward_one=0,
    reward_two=0,
    reward_mints=None,
    supports_limit_order=None,
):
    payload = {
            "position_address": "position",
            "pool_address": "pool",
            "owner": "owner",
            "fee_owner": "owner",
            "lower_bin_id": 100,
            "upper_bin_id": 100,
            "total_x_amount": "100",
            "total_y_amount": "200",
            "fee_x": str(fee_x),
            "fee_y": str(fee_y),
            "reward_one": str(reward_one),
            "reward_two": str(reward_two),
            "last_updated_at": 1,
            "total_claimed_fee_x_amount": claimed_x,
            "total_claimed_fee_y_amount": claimed_y,
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
                    "position_x_amount": "100",
                    "position_y_amount": "200",
                    "position_fee_x_amount": str(fee_x),
                    "position_fee_y_amount": str(fee_y),
                    "position_reward_amounts": [str(reward_one), str(reward_two)],
                }
            ],
        }
    if reward_checkpoint_one is not None or reward_checkpoint_two is not None:
        payload["bins"][0]["bin_reward_per_token_stored"] = [
            str(reward_checkpoint_one or 0),
            str(reward_checkpoint_two or 0),
        ]
    if reward_mints is not None:
        payload["reward_mints"] = list(reward_mints)
    if supports_limit_order is not None:
        payload["supports_limit_order"] = supports_limit_order
    storage.save_chain_position_snapshot(
        payload,
        observed_at=observed_at,
    )


def test_position_amount_reconciliation_matches_dynamic_position_output(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(
        storage,
        "2026-09-22T00:00:00+00:00",
        checkpoint_x=3 * Q64,
        checkpoint_y=5 * Q64,
        fee_x=7,
        fee_y=9,
    )

    result = reconcile_position_amounts(str(db), position_address="position")

    assert result.bins_checked == 1
    assert result.mismatched_bins == 0
    assert result.exact_match is True
    assert result.total_abs_error_x == 0
    assert result.total_abs_error_y == 0


def test_fee_reconciliation_matches_checkpoint_growth(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(
        storage,
        "2026-09-22T00:00:00+00:00",
        checkpoint_x=3 * Q64,
        checkpoint_y=5 * Q64,
        fee_x=7,
        fee_y=9,
    )
    save_position(
        storage,
        "2026-09-22T00:05:00+00:00",
        checkpoint_x=5 * Q64,
        checkpoint_y=8 * Q64,
        fee_x=27,
        fee_y=39,
    )

    result = reconcile_latest_position_fee_interval(
        str(db),
        position_address="position",
    )

    assert result.predicted_fee_x_delta == 20
    assert result.actual_fee_x_delta == 20
    assert result.predicted_fee_y_delta == 30
    assert result.actual_fee_y_delta == 30
    assert result.exact_match is True


def test_fee_reconciliation_rejects_claim_interval(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(
        storage,
        "2026-09-22T00:00:00+00:00",
        checkpoint_x=0,
        checkpoint_y=0,
        fee_x=0,
        fee_y=0,
        claimed_x="0",
    )
    save_position(
        storage,
        "2026-09-22T00:05:00+00:00",
        checkpoint_x=Q64,
        checkpoint_y=0,
        fee_x=0,
        fee_y=0,
        claimed_x="10",
    )

    with pytest.raises(ValueError, match="claimed fees changed"):
        reconcile_latest_position_fee_interval(
            str(db),
            position_address="position",
        )


def test_reconcile_position_includes_fee_interval_when_available(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(
        storage,
        "2026-09-22T00:00:00+00:00",
        checkpoint_x=0,
        checkpoint_y=0,
        fee_x=0,
        fee_y=0,
    )
    save_position(
        storage,
        "2026-09-22T00:05:00+00:00",
        checkpoint_x=Q64,
        checkpoint_y=Q64,
        fee_x=10,
        fee_y=10,
    )

    report = reconcile_position(str(db), position_address="position")
    assert report.amount_state.exact_match is True
    assert report.fee_interval is not None
    assert report.fee_interval.exact_match is True



def test_specific_fee_interval_can_reconcile_non_latest_pair(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(
        storage,
        "2026-09-22T00:00:00+00:00",
        checkpoint_x=0,
        checkpoint_y=0,
        fee_x=0,
        fee_y=0,
    )
    save_position(
        storage,
        "2026-09-22T00:05:00+00:00",
        checkpoint_x=Q64,
        checkpoint_y=2 * Q64,
        fee_x=10,
        fee_y=20,
    )
    save_position(
        storage,
        "2026-09-22T00:10:00+00:00",
        checkpoint_x=3 * Q64,
        checkpoint_y=5 * Q64,
        fee_x=30,
        fee_y=50,
    )

    result = reconcile_position_fee_interval(
        str(db),
        position_address="position",
        start_observed_at="2026-09-22T00:00:00+00:00",
        end_observed_at="2026-09-22T00:05:00+00:00",
    )

    assert result.predicted_fee_x_delta == 10
    assert result.predicted_fee_y_delta == 20
    assert result.exact_match is True



def test_reward_reconciliation_matches_dynamic_position_checkpoint_growth(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    reward_mints = ("reward-mint", "11111111111111111111111111111111")
    save_position(
        storage,
        "2026-09-22T00:00:00+00:00",
        checkpoint_x=0,
        checkpoint_y=0,
        fee_x=0,
        fee_y=0,
        reward_checkpoint_one=2 * Q64,
        reward_checkpoint_two=0,
        reward_one=7,
        reward_mints=reward_mints,
        supports_limit_order=False,
    )
    save_position(
        storage,
        "2026-09-22T00:05:00+00:00",
        checkpoint_x=0,
        checkpoint_y=0,
        fee_x=0,
        fee_y=0,
        reward_checkpoint_one=5 * Q64,
        reward_checkpoint_two=0,
        reward_one=37,
        reward_mints=reward_mints,
        supports_limit_order=False,
    )

    result = reconcile_latest_position_reward_interval(
        str(db),
        position_address="position",
    )

    assert result.predicted_reward_one_delta == 30
    assert result.actual_reward_one_delta == 30
    assert result.bins_with_checkpoint_growth == 1
    assert result.exact_match is True


def test_reward_reconciliation_rejects_legacy_checkpoint_snapshot(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    reward_mints = ("reward-mint", "11111111111111111111111111111111")
    save_position(
        storage,
        "2026-09-22T00:00:00+00:00",
        checkpoint_x=0,
        checkpoint_y=0,
        fee_x=0,
        fee_y=0,
        reward_mints=reward_mints,
        supports_limit_order=False,
    )
    save_position(
        storage,
        "2026-09-22T00:05:00+00:00",
        checkpoint_x=0,
        checkpoint_y=0,
        fee_x=0,
        fee_y=0,
        reward_mints=reward_mints,
        supports_limit_order=False,
    )

    with pytest.raises(ValueError, match="reward checkpoint metadata missing"):
        reconcile_latest_position_reward_interval(
            str(db),
            position_address="position",
        )


def test_reward_reconciliation_rejects_campaign_change(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(
        storage,
        "2026-09-22T00:00:00+00:00",
        checkpoint_x=0,
        checkpoint_y=0,
        fee_x=0,
        fee_y=0,
        reward_checkpoint_one=Q64,
        reward_mints=("reward-a", "11111111111111111111111111111111"),
        supports_limit_order=False,
    )
    save_position(
        storage,
        "2026-09-22T00:05:00+00:00",
        checkpoint_x=0,
        checkpoint_y=0,
        fee_x=0,
        fee_y=0,
        reward_checkpoint_one=2 * Q64,
        reward_one=10,
        reward_mints=("reward-b", "11111111111111111111111111111111"),
        supports_limit_order=False,
    )

    with pytest.raises(ValueError, match="reward campaign mint changed"):
        reconcile_position_reward_interval(
            str(db),
            position_address="position",
            start_observed_at="2026-09-22T00:00:00+00:00",
            end_observed_at="2026-09-22T00:05:00+00:00",
        )


def test_reconcile_position_reports_reward_ineligibility_without_hiding_it(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_position(
        storage,
        "2026-09-22T00:00:00+00:00",
        checkpoint_x=0,
        checkpoint_y=0,
        fee_x=0,
        fee_y=0,
    )
    save_position(
        storage,
        "2026-09-22T00:05:00+00:00",
        checkpoint_x=Q64,
        checkpoint_y=0,
        fee_x=10,
        fee_y=0,
    )

    report = reconcile_position(str(db), position_address="position")
    assert report.reward_interval is None
    assert "reward campaign metadata missing" in str(report.reward_interval_error)
