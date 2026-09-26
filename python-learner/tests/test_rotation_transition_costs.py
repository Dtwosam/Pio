from datetime import datetime, timedelta, timezone

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
    assert report.quote_eligible_transactions == 1
    assert report.total_network_fee_lamports == 7000
    assert report.total_network_fee_quote == 0.00014
    assert report.cost_components_complete is False
    assert report.included_cost_components == ("SOLANA_NETWORK_FEE",)
    sample = report.samples[0]
    assert sample.events_in_transaction == 2
    assert sample.reported_x_fee_amount == 9
    assert sample.reported_y_fee_amount == 6
    assert sample.reported_reward_one == 8
    assert sample.reported_reward_two == 10
    assert sample.network_fee_quote == 0.00014


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
