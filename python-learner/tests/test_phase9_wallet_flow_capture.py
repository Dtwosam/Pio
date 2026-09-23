from datetime import datetime, timedelta, timezone
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


def test_wallet_flow_capture_expands_current_owners_to_closed_positions(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    def collect(position):
        calls.append(position)
        owner = {
            "position-a": "owner-a",
            "closed-a": "owner-a",
            "position-b": "owner-b",
            "closed-b": "owner-b",
        }[position]
        return seed_events(
            storage,
            pool="pool-a",
            position=position,
            owner=owner,
            count=2,
            offset=len(calls) * 10,
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
        expand_owner_positions=lambda pool, owner: (
            ("position-a", "closed-a")
            if owner == "owner-a"
            else ("position-b", "closed-b")
        ),
        collect_history=collect,
    )

    assert report.source_after.ready is True
    assert report.source_scope == "CURRENT_OWNER_ALL_POSITION_COHORT"
    assert report.owners_expanded == 2
    assert report.owner_expansion_failures == 0
    assert report.expanded_positions_added == 2
    assert set(calls) == {
        "position-a",
        "position-b",
        "closed-a",
        "closed-b",
    }
    assert any(
        item.source == "API_OWNER_ALL"
        for item in report.items
    )


def test_wallet_flow_capture_round_robins_owners_before_extra_positions(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    def collect(position):
        calls.append(position)
        return 0

    report = run_phase9_wallet_flow_capture(
        storage,
        pool_address="pool-a",
        criteria=WalletFlowCriteria(
            min_events=100,
            min_unique_users=5,
        ),
        discover_positions=lambda pool, limit: discovery(
            pool,
            (
                ("position-a", "owner-a"),
                ("position-b", "owner-b"),
            ),
        ),
        expand_owner_positions=lambda pool, owner: (
            ("position-a", "closed-a-1", "closed-a-2")
            if owner == "owner-a"
            else ("position-b", "closed-b-1", "closed-b-2")
        ),
        collect_history=collect,
        max_positions_per_run=4,
    )

    assert report.positions_attempted == 4
    assert calls[:2] == ["position-a", "position-b"]
    assert set(calls[2:]) == {"closed-a-1", "closed-b-1"}


def test_wallet_flow_capture_isolates_owner_expansion_failure(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def expand(pool, owner):
        if owner == "owner-a":
            raise RuntimeError("pnl unavailable")
        return ("position-b", "closed-b")

    report = run_phase9_wallet_flow_capture(
        storage,
        pool_address="pool-a",
        criteria=WalletFlowCriteria(
            min_events=100,
            min_unique_users=5,
        ),
        discover_positions=lambda pool, limit: discovery(
            pool,
            (
                ("position-a", "owner-a"),
                ("position-b", "owner-b"),
            ),
        ),
        expand_owner_positions=expand,
        collect_history=lambda position: 0,
    )

    assert report.owners_expanded == 1
    assert report.owner_expansion_failures == 1
    assert report.expanded_positions_added == 1
    assert any(
        "owner position expansion(s) failed" in reason
        and "pnl unavailable" in reason
        for reason in report.reasons
    )


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
        "not a complete historical pool census" in reason
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


def test_wallet_flow_source_state_uses_same_latest_event_window_as_research(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    base = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    events = []

    # Five older users exist in lifetime history but sit outside the latest
    # 500-event research window.
    for index in range(5):
        created = (base + timedelta(seconds=index)).isoformat()
        events.append(
            {
                "observed_at": created,
                "position_address": f"old-position-{index}",
                "signature": f"old-sig-{index}",
                "ix_index": 0,
                "event_type": "ADD_LIQUIDITY",
                "block_time": 1_795_000_000 + index,
                "slot": 1000 + index,
                "pool_address": "pool-a",
                "user_address": f"old-user-{index}",
                "token_x": "x",
                "token_y": "y",
                "amount_x": "1",
                "amount_y": "1",
                "amount_x_usd": "1",
                "amount_y_usd": "1",
                "total_usd": "2",
                "created_at": created,
                "raw": {},
            }
        )

    for index in range(500):
        created = (
            base + timedelta(minutes=1, seconds=index)
        ).isoformat()
        events.append(
            {
                "observed_at": created,
                "position_address": "recent-position",
                "signature": f"recent-sig-{index}",
                "ix_index": 0,
                "event_type": "ADD_LIQUIDITY",
                "block_time": 1_795_001_000 + index,
                "slot": 2000 + index,
                "pool_address": "pool-a",
                "user_address": "recent-user",
                "token_x": "x",
                "token_y": "y",
                "amount_x": "1",
                "amount_y": "1",
                "amount_x_usd": "1",
                "amount_y_usd": "1",
                "total_usd": "2",
                "created_at": created,
                "raw": {},
            }
        )
    storage.save_position_events(events)

    state = wallet_flow_source_state(
        storage,
        pool_address="pool-a",
        criteria=WalletFlowCriteria(
            lookback_events=500,
            min_events=20,
            min_unique_users=5,
        ),
    )

    assert state.events == 500
    assert state.unique_users == 1
    assert state.positions == 1
    assert state.ready is False


def test_wallet_flow_source_state_honors_as_of_inside_lookback_window(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_events(
        storage,
        pool="pool-a",
        position="position-a",
        owner="owner-a",
        count=5,
    )
    seed_events(
        storage,
        pool="pool-a",
        position="position-b",
        owner="owner-b",
        count=5,
        offset=10,
        created_at_prefix="2026-09-23T14:00:",
    )

    state = wallet_flow_source_state(
        storage,
        pool_address="pool-a",
        criteria=WalletFlowCriteria(
            lookback_events=20,
            min_events=10,
            min_unique_users=2,
        ),
        as_of="2026-09-23T13:00:00+00:00",
    )

    assert state.events == 5
    assert state.unique_users == 1
    assert state.ready is False
