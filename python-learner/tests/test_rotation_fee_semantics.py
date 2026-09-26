from meteora_learner.rotation_fee_semantics import (
    build_rotation_fee_semantics_report,
)
from meteora_learner.storage import Storage


POOL = "pool"
POSITION = "position"
OWNER = "owner"
X_MINT = "x-mint"
Y_MINT = "y-mint"


def save_pool_identity(storage, *, x_mint=X_MINT, y_mint=Y_MINT, observed_at="2026-09-26T12:00:00+00:00"):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(
                observed_at, pool_address, active_bin_id, bin_step,
                token_x_mint, token_y_mint, raw_json
            ) VALUES (?, ?, 10, 25, ?, ?, '{}')
            """,
            (observed_at, POOL, x_mint, y_mint),
        )


def rebalance_event(*, event_index=0, parent_ix_index=3, fee_x=5, fee_y=7):
    return {
        "event_index": event_index,
        "parent_ix_index": parent_ix_index,
        "event": {
            "event_type": "Rebalancing",
            "event": {
                "lb_pair": POOL,
                "position": POSITION,
                "owner": OWNER,
                "active_bin_id": 10,
                "x_withdrawn_amount": "100",
                "x_added_amount": "80",
                "y_withdrawn_amount": "200",
                "y_added_amount": "170",
                "x_fee_amount": str(fee_x),
                "y_fee_amount": str(fee_y),
                "old_min_id": 1,
                "old_max_id": 5,
                "new_min_id": 8,
                "new_max_id": 12,
                "reward_one": "0",
                "reward_two": "0",
            },
        },
    }


def token_delta(*, account_index, mint, delta, owner_change=False):
    pre = 100
    post = pre + delta
    assert post >= 0
    return {
        "account_index": account_index,
        "account_address": f"account-{account_index}",
        "mint": mint,
        "pre_owner": OWNER,
        "post_owner": "other-owner" if owner_change else OWNER,
        "pre_amount": str(pre),
        "post_amount": str(post),
        "delta_amount": str(delta),
        "decimals": 6,
    }


def rebalance_request(*, instruction_index=3, should_claim_fee=True):
    return {
        "instruction_index": instruction_index,
        "observed_active_id": 10,
        "max_active_bin_slippage": 2,
        "should_claim_fee": should_claim_fee,
        "should_claim_reward": False,
        "min_withdraw_x_amount": "0",
        "max_deposit_x_amount": "1000",
        "min_withdraw_y_amount": "0",
        "max_deposit_y_amount": "1000",
        "shrink_mode": 0,
    }


def save_rebalance(
    storage,
    *,
    owner_x_delta,
    owner_y_delta,
    should_claim_fee,
    fee_x=5,
    fee_y=7,
    owner_change=False,
    extra_rebalance=False,
    signature="sig",
):
    events = [rebalance_event(fee_x=fee_x, fee_y=fee_y)]
    requests = [
        rebalance_request(should_claim_fee=should_claim_fee)
    ]
    if extra_rebalance:
        events.append(
            rebalance_event(
                event_index=1,
                parent_ix_index=4,
                fee_x=1,
                fee_y=1,
            )
        )
        requests.append(
            rebalance_request(
                instruction_index=4,
                should_claim_fee=should_claim_fee,
            )
        )

    storage.save_chain_transaction_events(
        {
            "signature": signature,
            "slot": 450700000,
            "block_time": 1_700_000_000,
            "network_fee_lamports": 5000,
            "compute_units_consumed": 200000,
            "succeeded": True,
            "token_balance_deltas": [
                token_delta(
                    account_index=1,
                    mint=X_MINT,
                    delta=owner_x_delta,
                    owner_change=owner_change,
                ),
                token_delta(
                    account_index=2,
                    mint=Y_MINT,
                    delta=owner_y_delta,
                ),
            ],
            "add_requests": [],
            "rebalance_requests": requests,
            "events": events,
        }
    )


def test_fee_separate_hypothesis_matches_owner_flow_exactly(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool_identity(storage)
    save_rebalance(
        storage,
        owner_x_delta=25,
        owner_y_delta=37,
        should_claim_fee=True,
    )

    report = build_rotation_fee_semantics_report(
        storage,
        position_address=POSITION,
    )

    assert report.transactions_seen == 1
    assert report.eligible_transactions == 1
    assert report.claim_fee_true_samples == 1
    assert report.fee_separate_only_matches == 1
    assert report.base_flow_only_matches == 0
    sample = report.samples[0]
    assert sample.base_flow_x == 20
    assert sample.base_flow_y == 30
    assert sample.base_residual_x == 5
    assert sample.base_residual_y == 7
    assert sample.fee_separate_residual_x == 0
    assert sample.fee_separate_residual_y == 0
    assert sample.evidence_class == "FEE_SEPARATE_ONLY_EXACT"
    assert report.semantics_resolved is False


def test_base_flow_hypothesis_can_match_when_claim_fee_false(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool_identity(storage)
    save_rebalance(
        storage,
        owner_x_delta=20,
        owner_y_delta=30,
        should_claim_fee=False,
    )

    report = build_rotation_fee_semantics_report(
        storage,
        position_address=POSITION,
    )

    assert report.claim_fee_false_samples == 1
    assert report.base_flow_only_matches == 1
    assert report.samples[0].evidence_class == "BASE_FLOW_ONLY_EXACT"
    assert report.conclusion == "UNRESOLVED_OBSERVATIONAL_EVIDENCE"


def test_zero_reported_fee_makes_both_hypotheses_identical(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool_identity(storage)
    save_rebalance(
        storage,
        owner_x_delta=20,
        owner_y_delta=30,
        should_claim_fee=True,
        fee_x=0,
        fee_y=0,
    )

    report = build_rotation_fee_semantics_report(
        storage,
        position_address=POSITION,
    )

    assert report.both_hypotheses_match == 1
    assert report.samples[0].evidence_class == "BOTH_HYPOTHESES_EXACT"


def test_multiple_rebalance_events_are_not_attributed_to_shared_owner_flow(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool_identity(storage)
    save_rebalance(
        storage,
        owner_x_delta=25,
        owner_y_delta=37,
        should_claim_fee=True,
        extra_rebalance=True,
    )

    report = build_rotation_fee_semantics_report(
        storage,
        position_address=POSITION,
    )

    assert report.transactions_seen == 1
    assert report.eligible_transactions == 0
    assert all(not item.eligible for item in report.samples)
    assert all(
        "multiple Rebalancing events" in str(item.exclusion_reason)
        for item in report.samples
    )


def test_owner_change_blocks_token_flow_attribution(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool_identity(storage)
    save_rebalance(
        storage,
        owner_x_delta=25,
        owner_y_delta=37,
        should_claim_fee=True,
        owner_change=True,
    )

    report = build_rotation_fee_semantics_report(
        storage,
        position_address=POSITION,
    )

    assert report.eligible_transactions == 0
    assert "owner changed" in str(report.samples[0].exclusion_reason)


def test_pool_mint_identity_change_blocks_semantics_attribution(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool_identity(storage)
    save_pool_identity(
        storage,
        x_mint="different-x",
        observed_at="2026-09-26T12:05:00+00:00",
    )
    save_rebalance(
        storage,
        owner_x_delta=25,
        owner_y_delta=37,
        should_claim_fee=True,
    )

    report = build_rotation_fee_semantics_report(
        storage,
        position_address=POSITION,
    )

    assert report.eligible_transactions == 0
    assert "mint identity changed" in str(
        report.samples[0].exclusion_reason
    )
