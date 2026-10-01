from __future__ import annotations

import pytest

from meteora_learner.live_position_transition import (
    record_live_position_transition,
)
from meteora_learner.storage import Storage


def _insert_position(
    storage: Storage,
    *,
    position: str,
    pool: str,
    status: str,
    opened_decision: str,
    opened_at: str,
    closed_decision: str | None = None,
    last_observed_at: str | None = None,
) -> None:
    last_decision = closed_decision or opened_decision
    last_signature = f"SIG-{last_decision}"
    observed_at = last_observed_at or opened_at
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_positions(
                position_address, pool_address, status,
                opened_decision_id, opened_signature, opened_at,
                min_bin_id, max_bin_id,
                last_decision_id, last_signature, last_observed_at,
                rebalances, closed_decision_id, closed_signature,
                raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, '{}')
            """,
            (
                position,
                pool,
                status,
                opened_decision,
                f"SIG-{opened_decision}",
                opened_at,
                -2,
                2,
                last_decision,
                last_signature,
                observed_at,
                closed_decision,
                (
                    f"SIG-{closed_decision}"
                    if closed_decision is not None
                    else None
                ),
            ),
        )


def _event(
    storage: Storage,
    *,
    decision: str,
    position: str,
    event_time: str,
    action: str,
    prior: str | None,
    next_status: str,
) -> None:
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_position_events(
                decision_id, signature, position_address,
                event_time, action, prior_status, next_status,
                min_bin_id, max_bin_id, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, -2, 2, '{}')
            """,
            (
                decision,
                f"SIG-{decision}",
                position,
                event_time,
                action,
                prior,
                next_status,
            ),
        )


def _seed_transition(
    storage: Storage,
    *,
    next_pool: str = "POOL-B",
    next_enter_at: str = "2026-01-01T02:00:00+00:00",
) -> None:
    _insert_position(
        storage,
        position="OLD",
        pool="POOL-A",
        status="CLOSED",
        opened_decision="OLD-ENTER",
        opened_at="2026-01-01T00:00:00+00:00",
        closed_decision="OLD-EXIT",
        last_observed_at="2026-01-01T01:00:00+00:00",
    )
    _event(
        storage,
        decision="OLD-ENTER",
        position="OLD",
        event_time="2026-01-01T00:00:00+00:00",
        action="ENTER",
        prior=None,
        next_status="OPEN",
    )
    _event(
        storage,
        decision="OLD-EXIT",
        position="OLD",
        event_time="2026-01-01T01:00:00+00:00",
        action="EXIT",
        prior="OPEN",
        next_status="LIQUIDITY_REMOVED",
    )

    _insert_position(
        storage,
        position="NEW",
        pool=next_pool,
        status="OPEN",
        opened_decision="NEW-ENTER",
        opened_at=next_enter_at,
    )
    _event(
        storage,
        decision="NEW-ENTER",
        position="NEW",
        event_time=next_enter_at,
        action="ENTER",
        prior=None,
        next_status="OPEN",
    )


def test_records_explicit_pool_switch_and_reuses_exact_link(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed_transition(storage)

    first = record_live_position_transition(
        storage,
        previous_position_address="OLD",
        next_position_address="NEW",
    )
    second = record_live_position_transition(
        storage,
        previous_position_address="OLD",
        next_position_address="NEW",
    )

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert first.transition == second.transition
    assert first.transition.transition_kind == "POOL_SWITCH"
    assert (
        first.transition.annotation_source
        == "EXPLICIT_RESEARCH_REVIEW"
    )

    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM live_position_transitions"
        ).fetchone()[0]
    assert rows == 1


def test_records_same_pool_reentry_without_inference(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed_transition(storage, next_pool="POOL-A")

    result = record_live_position_transition(
        storage,
        previous_position_address="OLD",
        next_position_address="NEW",
    )

    assert result.transition.transition_kind == "SAME_POOL_REENTRY"
    assert result.transition.previous_exit_decision_id == "OLD-EXIT"
    assert result.transition.next_enter_decision_id == "NEW-ENTER"


def test_rejects_non_monotonic_transition(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed_transition(
        storage,
        next_enter_at="2026-01-01T00:30:00+00:00",
    )

    with pytest.raises(
        ValueError,
        match="must occur after previous EXIT",
    ):
        record_live_position_transition(
            storage,
            previous_position_address="OLD",
            next_position_address="NEW",
        )


def test_rejects_open_predecessor(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed_transition(storage)
    with storage.connect() as conn:
        conn.execute(
            "UPDATE live_positions SET status = 'OPEN' "
            "WHERE position_address = 'OLD'"
        )

    with pytest.raises(ValueError, match="must be CLOSED"):
        record_live_position_transition(
            storage,
            previous_position_address="OLD",
            next_position_address="NEW",
        )


def test_rejects_reusing_position_in_different_transition(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed_transition(storage)
    record_live_position_transition(
        storage,
        previous_position_address="OLD",
        next_position_address="NEW",
    )

    _insert_position(
        storage,
        position="NEW2",
        pool="POOL-C",
        status="OPEN",
        opened_decision="NEW2-ENTER",
        opened_at="2026-01-01T03:00:00+00:00",
    )
    _event(
        storage,
        decision="NEW2-ENTER",
        position="NEW2",
        event_time="2026-01-01T03:00:00+00:00",
        action="ENTER",
        prior=None,
        next_status="OPEN",
    )

    with pytest.raises(ValueError, match="already linked"):
        record_live_position_transition(
            storage,
            previous_position_address="OLD",
            next_position_address="NEW2",
        )


def test_transition_rows_are_immutable(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed_transition(storage)
    result = record_live_position_transition(
        storage,
        previous_position_address="OLD",
        next_position_address="NEW",
    )

    with storage.connect() as conn:
        with pytest.raises(Exception, match="immutable"):
            conn.execute(
                """
                UPDATE live_position_transitions
                SET transition_kind = 'SAME_POOL_REENTRY'
                WHERE transition_id = ?
                """,
                (result.transition.transition_id,),
            )
