from __future__ import annotations

import json

import pytest

from meteora_learner.live_position_transition import (
    record_live_position_transition,
)
from meteora_learner.live_transition_annotation_backlog import (
    build_live_transition_annotation_backlog,
)
from meteora_learner.live_transition_annotation_backlog_cli import (
    main as backlog_main,
)
from meteora_learner.storage import Storage


def _position(
    storage: Storage,
    *,
    position: str,
    pool: str,
    status: str,
    enter_decision: str,
    enter_at: str,
    exit_decision: str | None = None,
    exit_at: str | None = None,
) -> None:
    last_decision = exit_decision or enter_decision
    last_at = exit_at or enter_at
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
            ) VALUES (?, ?, ?, ?, ?, ?, -2, 2, ?, ?, ?, 0, ?, ?, '{}')
            """,
            (
                position,
                pool,
                status,
                enter_decision,
                f"SIG-{enter_decision}",
                enter_at,
                last_decision,
                f"SIG-{last_decision}",
                last_at,
                exit_decision,
                (
                    f"SIG-{exit_decision}"
                    if exit_decision is not None
                    else None
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO live_position_events(
                decision_id, signature, position_address,
                event_time, action, prior_status, next_status,
                min_bin_id, max_bin_id, raw_json
            ) VALUES (?, ?, ?, ?, 'ENTER', NULL, 'OPEN', -2, 2, '{}')
            """,
            (
                enter_decision,
                f"SIG-{enter_decision}",
                position,
                enter_at,
            ),
        )
        if exit_decision is not None:
            conn.execute(
                """
                INSERT INTO live_position_events(
                    decision_id, signature, position_address,
                    event_time, action, prior_status, next_status,
                    min_bin_id, max_bin_id, raw_json
                ) VALUES (?, ?, ?, ?, 'EXIT', 'OPEN',
                          'LIQUIDITY_REMOVED', -2, 2, '{}')
                """,
                (
                    exit_decision,
                    f"SIG-{exit_decision}",
                    position,
                    exit_at,
                ),
            )


def _seed(storage: Storage) -> None:
    _position(
        storage,
        position="A",
        pool="POOL-A",
        status="CLOSED",
        enter_decision="A-ENTER",
        enter_at="2026-01-01T00:00:00+00:00",
        exit_decision="A-EXIT",
        exit_at="2026-01-01T01:00:00+00:00",
    )
    _position(
        storage,
        position="B",
        pool="POOL-B",
        status="CLOSED",
        enter_decision="B-ENTER",
        enter_at="2026-01-01T02:00:00+00:00",
        exit_decision="B-EXIT",
        exit_at="2026-01-01T03:00:00+00:00",
    )
    _position(
        storage,
        position="C",
        pool="POOL-C",
        status="OPEN",
        enter_decision="C-ENTER",
        enter_at="2026-01-01T04:00:00+00:00",
    )


def test_backlog_keeps_predecessor_and_successor_lists_separate(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage)
    record_live_position_transition(
        storage,
        previous_position_address="A",
        next_position_address="B",
    )

    report = build_live_transition_annotation_backlog(
        str(storage.path)
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.transition_pairs_inferred is False
    assert report.pair_suggestions_emitted is False
    assert report.economic_ranking_applied is False
    assert report.existing_links == 1

    assert [
        item.position_address
        for item in report.predecessor_candidates
    ] == ["B"]
    assert [
        item.position_address
        for item in report.successor_candidates
    ] == ["A", "C"]
    assert report.predecessor_candidates_seen == 1
    assert report.successor_candidates_seen == 2

    record = report.to_record()
    assert "suggested_pairs" not in record
    assert "recommended_pair" not in record
    assert "score" not in record


def test_backlog_empty_database_is_descriptive(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")

    report = build_live_transition_annotation_backlog(
        str(storage.path)
    )

    assert report.existing_links == 0
    assert report.predecessor_candidates == ()
    assert report.successor_candidates == ()
    assert report.predecessor_candidates_seen == 0
    assert report.successor_candidates_seen == 0


def test_backlog_rejects_ambiguous_duplicate_lifecycle_events(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _position(
        storage,
        position="A",
        pool="POOL-A",
        status="CLOSED",
        enter_decision="A-ENTER",
        enter_at="2026-01-01T00:00:00+00:00",
        exit_decision="A-EXIT",
        exit_at="2026-01-01T01:00:00+00:00",
    )
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_position_events(
                decision_id, signature, position_address,
                event_time, action, prior_status, next_status,
                min_bin_id, max_bin_id, raw_json
            ) VALUES (
                'A-EXIT-2', 'SIG-A-EXIT-2', 'A',
                '2026-01-01T01:01:00+00:00',
                'EXIT', 'OPEN', 'LIQUIDITY_REMOVED',
                -2, 2, '{}'
            )
            """
        )

    with pytest.raises(
        ValueError,
        match="predecessor backlog has duplicate position",
    ):
        build_live_transition_annotation_backlog(str(storage.path))


def test_backlog_cli_is_read_only(tmp_path, capsys) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage)
    before = storage.path.read_bytes()

    code = backlog_main(["--database", str(storage.path)])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["research_only"] is True
    assert payload["policy_actionable"] is False
    assert payload["execution_wired"] is False
    assert payload["transition_pairs_inferred"] is False
    assert payload["pair_suggestions_emitted"] is False
    assert payload["economic_ranking_applied"] is False
    assert storage.path.read_bytes() == before

    with storage.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM live_position_transitions"
        ).fetchone()[0] == 0
