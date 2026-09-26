from datetime import datetime, timedelta, timezone

import pytest

from meteora_learner.quote_registry import save_token_quote
from meteora_learner.rotation_transition_costs import (
    WRAPPED_SOL_MINT,
    build_quote_normalized_rotation_cost_report,
)
from meteora_learner.storage import Storage


BLOCK_TIME = 1_700_000_000


def snapshot(*, signature="sig", block_time=BLOCK_TIME, duplicate_event=False):
    event = {
        "event_index": 0,
        "parent_ix_index": 3,
        "event": {
            "event_type": "Rebalancing",
            "event": {
                "lb_pair": "pool",
                "position": "position",
                "owner": "owner",
                "active_bin_id": 11,
                "x_withdrawn_amount": "110",
                "x_added_amount": "80",
                "y_withdrawn_amount": "210",
                "y_added_amount": "170",
                "x_fee_amount": "2",
                "y_fee_amount": "3",
                "old_min_id": 1,
                "old_max_id": 5,
                "new_min_id": 9,
                "new_max_id": 13,
                "reward_one": "4",
                "reward_two": "5",
            },
        },
    }
    events = [event]
    if duplicate_event:
        second = {
            **event,
            "event_index": 1,
            "event": {
                **event["event"],
                "event": {
                    **event["event"]["event"],
                    "x_fee_amount": "7",
                },
            },
        }
        events.append(second)
    return {
        "signature": signature,
        "slot": 120,
        "block_time": block_time,
        "network_fee_lamports": 7000,
        "compute_units_consumed": 220000,
        "succeeded": True,
        "rebalance_requests": [],
        "events": events,
    }


def test_rotation_network_fee_is_quote_normalized_without_counting_protocol_fields(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_transaction_events(snapshot(duplicate_event=True))
    observed = datetime.fromtimestamp(
        BLOCK_TIME, tz=timezone.utc
    ) - timedelta(seconds=60)
    save_token_quote(
        storage,
        token_mint=WRAPPED_SOL_MINT,
        quote_per_atomic=0.00000002,
        source="TEST",
        observed_at=observed.isoformat(),
    )

    report = build_quote_normalized_rotation_cost_report(
        storage,
        position_address="position",
        max_quote_age_seconds=300,
    )

    assert report.rotation_transactions == 1
    assert report.quote_unit == "ACCOUNT_QUOTE"
    assert report.quote_eligible_transactions == 1
    assert report.total_network_fee_lamports == 7000
    assert report.total_network_fee_quote == pytest.approx(0.00014)
    assert report.cost_components_complete is False
    assert report.included_cost_components == ("SOLANA_NETWORK_FEE",)
    sample = report.samples[0]
    assert sample.events_in_transaction == 2
    assert sample.sol_quote_source == "TEST"
    assert sample.reported_x_fee_amount == 9
    assert sample.reported_y_fee_amount == 6
    assert sample.reported_reward_one == 8
    assert sample.reported_reward_two == 10
    assert sample.network_fee_quote == pytest.approx(0.00014)


def test_rotation_cost_marks_stale_quote_ineligible(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_transaction_events(snapshot())
    observed = datetime.fromtimestamp(
        BLOCK_TIME, tz=timezone.utc
    ) - timedelta(seconds=301)
    save_token_quote(
        storage,
        token_mint=WRAPPED_SOL_MINT,
        quote_per_atomic=0.00000002,
        source="TEST",
        observed_at=observed.isoformat(),
    )

    report = build_quote_normalized_rotation_cost_report(
        storage,
        position_address="position",
        max_quote_age_seconds=300,
    )

    assert report.quote_eligible_transactions == 0
    assert report.quote_coverage_rate == 0.0
    assert report.total_network_fee_quote == 0.0
    assert report.samples[0].exclusion_reason == (
        "SOL quote stale at transaction time"
    )


def test_rotation_cost_deduplicates_network_fee_by_signature(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_transaction_events(snapshot(duplicate_event=True))
    observed = datetime.fromtimestamp(BLOCK_TIME, tz=timezone.utc)
    save_token_quote(
        storage,
        token_mint=WRAPPED_SOL_MINT,
        quote_per_atomic=1.0,
        source="TEST",
        observed_at=observed.isoformat(),
    )

    report = build_quote_normalized_rotation_cost_report(
        storage,
        position_address="position",
    )

    assert report.rotation_transactions == 1
    assert report.total_network_fee_lamports == 7000
    assert report.total_network_fee_quote == 7000.0



def test_rotation_owner_token_flows_are_quote_normalized_separately_from_cost(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    payload = snapshot()
    payload["token_balance_deltas"] = [
        {
            "account_index": 2,
            "account_address": "x-account",
            "mint": "mint-x",
            "pre_owner": "owner",
            "post_owner": "owner",
            "pre_amount": "100",
            "post_amount": "130",
            "delta_amount": "30",
            "decimals": 6,
        },
        {
            "account_index": 3,
            "account_address": "y-account",
            "mint": "mint-y",
            "pre_owner": "owner",
            "post_owner": "owner",
            "pre_amount": "200",
            "post_amount": "180",
            "delta_amount": "-20",
            "decimals": 6,
        },
    ]
    storage.save_chain_transaction_events(payload)
    observed = datetime.fromtimestamp(BLOCK_TIME, tz=timezone.utc)
    for mint, quote in (
        (WRAPPED_SOL_MINT, 0.00000002),
        ("mint-x", 2.0),
        ("mint-y", 1.0),
    ):
        save_token_quote(
            storage,
            token_mint=mint,
            quote_per_atomic=quote,
            source="TEST",
            observed_at=observed.isoformat(),
        )

    report = build_quote_normalized_rotation_cost_report(
        storage,
        position_address="position",
    )

    assert report.owner_token_flow_mints == 2
    assert report.owner_token_flow_quote_eligible_mints == 2
    assert report.owner_token_flow_quote_coverage_rate == 1.0
    assert report.quoted_owner_token_flow_net == pytest.approx(40.0)
    assert report.total_network_fee_quote == pytest.approx(0.00014)
    assert report.cost_components_complete is False

    sample = report.samples[0]
    assert sample.owner_address == "owner"
    by_mint = {item.mint: item for item in sample.owner_token_flows}
    assert by_mint["mint-x"].atomic_delta == 30
    assert by_mint["mint-x"].quote_value == pytest.approx(60.0)
    assert by_mint["mint-x"].quote_source == "TEST"
    assert by_mint["mint-y"].atomic_delta == -20
    assert by_mint["mint-y"].quote_value == pytest.approx(-20.0)


def test_rotation_owner_token_flow_rejects_owner_change_attribution(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    payload = snapshot()
    payload["token_balance_deltas"] = [
        {
            "account_index": 2,
            "account_address": "token-account",
            "mint": "mint-x",
            "pre_owner": "owner",
            "post_owner": "other-owner",
            "pre_amount": "100",
            "post_amount": "130",
            "delta_amount": "30",
            "decimals": 6,
        }
    ]
    storage.save_chain_transaction_events(payload)
    observed = datetime.fromtimestamp(BLOCK_TIME, tz=timezone.utc)
    save_token_quote(
        storage,
        token_mint=WRAPPED_SOL_MINT,
        quote_per_atomic=0.00000002,
        source="TEST",
        observed_at=observed.isoformat(),
    )
    save_token_quote(
        storage,
        token_mint="mint-x",
        quote_per_atomic=2.0,
        source="TEST",
        observed_at=observed.isoformat(),
    )

    report = build_quote_normalized_rotation_cost_report(
        storage,
        position_address="position",
    )

    assert report.owner_token_flow_mints == 1
    assert report.owner_token_flow_quote_eligible_mints == 0
    assert report.owner_token_flow_quote_coverage_rate == 0.0
    assert report.quoted_owner_token_flow_net == 0.0
    flow = report.samples[0].owner_token_flows[0]
    assert flow.attribution_eligible is False
    assert flow.quote_eligible is False
    assert flow.quote_value is None
    assert flow.exclusion_reason == (
        "token account owner changed during transaction"
    )
