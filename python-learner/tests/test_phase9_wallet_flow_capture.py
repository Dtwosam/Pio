from meteora_learner.phase9_position_discovery import (
    Phase9PositionDiscoveryItem,
    Phase9PositionDiscoveryReport,
)
from meteora_learner.phase9_wallet_flow_capture import (
    run_phase9_wallet_flow_capture,
    wallet_flow_source_state,
)
from meteora_learner.storage import Storage
from meteora_learner.wallet_flow import WalletFlowCriteria


def discovery(pool, positions, *, truncated=False, found=None):
    items = tuple(
        Phase9PositionDiscoveryItem(
            position_address=position,
            pool_address=pool,
            owner=owner,
            lower_bin_id=-1,
            upper_bin_id=1,
        )
        for position, owner in positions
    )
    total = len(items) if found is None else found
    return Phase9PositionDiscoveryReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        source_scope="CURRENT_ONCHAIN_POSITION_COHORT",
        pool_address=pool,
        positions_found=total,
        positions_returned=len(items),
        truncated=truncated,
        unique_owners=len({item.owner for item in items}),
        positions=items,
    )


def seed_events(
    storage,
    *,
    pool,
    position,
    owner,
    count,
    offset=0,
    created_at_prefix="2026-09-23T12:00:",
):
    events = []
    for index in range(count):
        value = offset + index
        events.append(
            {
                "observed_at": "2026-09-23T13:00:00+00:00",
                "position_address": position,
                "signature": f"sig-{position}-{value}",
                "ix_index": 0,
                "event_type": "ADD_LIQUIDITY",
                "block_time": 1_795_000_000 + value,
                "slot": 1000 + value,
                "pool_address": pool,
                "user_address": owner,
                "token_x": "x",
                "token_y": "y",
                "amount_x": "1",
                "amount_y": "1",
                "amount_x_usd": "1",
                "amount_y_usd": "1",
                "total_usd": "2",
                "created_at": (
                    f"{created_at_prefix}{value % 60:02d}+00:00"
                ),
                "raw": {},
            }
        )
    storage.save_position_events(events)
    return count


def test_wallet_flow_source_state_counts_events_users_and_positions(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_events(
        storage,
        pool="pool-a",
        position="position-a",
        owner="owner-a",
        count=3,
    )
    seed_events(
        storage,
        pool="pool-a",
        position="position-b",
        owner="owner-b",
        count=2,
        offset=10,
    )

    state = wallet_flow_source_state(
        storage,
        pool_address="pool-a",
        criteria=WalletFlowCriteria(
            min_events=5,
            min_unique_users=2,
        ),
    )

    assert state.events == 5
    assert state.unique_users == 2
    assert state.positions == 2
    assert state.ready is True


def test_wallet_flow_capture_stops_after_default_source_thresholds(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    positions = tuple(
        (f"position-{index}", f"owner-{index}")
        for index in range(6)
    )
    calls = []

    def collect(position):
        calls.append(position)
        index = int(position.split("-")[-1])
        return seed_events(
            storage,
            pool="pool-a",
            position=position,
            owner=f"owner-{index}",
            count=4,
            offset=index * 10,
        )

    report = run_phase9_wallet_flow_capture(
        storage,
        pool_address="pool-a",
        discover_positions=lambda pool, limit: discovery(
            pool,
            positions,
        ),
        collect_history=collect,
    )

    assert report.source_before.ready is False
    assert report.source_after.ready is True
    assert report.source_after.events == 20
    assert report.source_after.unique_users == 5
    assert report.positions_attempted == 5
    assert report.positions_refreshed == 5
    assert report.positions_failed == 0
    assert len(calls) == 5
    assert report.source_scope == "CURRENT_ONCHAIN_POSITION_COHORT"
    assert report.research_only is True
    assert report.read_only_capture is True
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_wallet_flow_capture_prioritizes_new_owner_before_existing_source(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_events(
        storage,
        pool="pool-a",
        position="position-a",
        owner="owner-a",
        count=4,
    )
    calls = []

    def collect(position):
        calls.append(position)
        owner = "owner-b" if position == "position-b" else "owner-a"
        return seed_events(
            storage,
            pool="pool-a",
            position=position,
            owner=owner,
            count=4,
            offset=20 if owner == "owner-b" else 30,
        )

    report = run_phase9_wallet_flow_capture(
        storage,
        pool_address="pool-a",
        criteria=WalletFlowCriteria(
            min_events=8,
            min_unique_users=2,
        ),
        discover_positions=lambda pool, limit: discovery(
            pool,
            (
                ("position-a", "owner-a"),
                ("position-b", "owner-b"),
            ),
        ),
        collect_history=collect,
    )

    assert report.source_after.ready is True
    assert calls == ["position-b"]
    assert report.positions_attempted == 1


def test_wallet_flow_capture_isolates_position_history_failure(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def collect(position):
        if position == "position-a":
            raise RuntimeError("data api unavailable")
        return seed_events(
            storage,
            pool="pool-a",
            position=position,
            owner="owner-b",
            count=2,
        )

    report = run_phase9_wallet_flow_capture(
        storage,
        pool_address="pool-a",
        criteria=WalletFlowCriteria(
            min_events=4,
            min_unique_users=2,
        ),
        discover_positions=lambda pool, limit: discovery(
            pool,
            (
                ("position-a", "owner-a"),
                ("position-b", "owner-b"),
            ),
        ),
        collect_history=collect,
    )

    assert report.source_after.ready is False
    assert report.positions_failed == 1
    assert report.positions_refreshed == 1
    failed = next(
        item for item in report.items
        if item.position_address == "position-a"
    )
    assert "data api unavailable" in failed.error
    assert any(
        "current-position cohort is not a complete historical pool census"
        in reason
        for reason in report.reasons
    )


def test_wallet_flow_capture_reports_empty_current_position_cohort(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = run_phase9_wallet_flow_capture(
        storage,
        pool_address="pool-a",
        discover_positions=lambda pool, limit: discovery(pool, ()),
        collect_history=lambda position: 0,
    )

    assert report.source_after.ready is False
    assert report.positions_attempted == 0
    assert any(
        "no current on-chain PositionV2 accounts" in reason
        for reason in report.reasons
    )


def test_wallet_flow_capture_noops_when_source_is_already_ready(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for index in range(5):
        seed_events(
            storage,
            pool="pool-a",
            position=f"position-{index}",
            owner=f"owner-{index}",
            count=4,
            offset=index * 10,
        )
    calls = []

    report = run_phase9_wallet_flow_capture(
        storage,
        pool_address="pool-a",
        discover_positions=lambda pool, limit: calls.append((pool, limit)),
        collect_history=lambda position: 0,
    )

    assert report.source_before.ready is True
    assert report.source_after.ready is True
    assert calls == []
    assert report.positions_attempted == 0
