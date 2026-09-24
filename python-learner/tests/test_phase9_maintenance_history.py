import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from meteora_learner import cli

from meteora_learner.phase9_maintenance_history import (
    list_phase9_maintenance_events,
    record_phase9_maintenance_event,
)
from meteora_learner.storage import Storage


def test_phase9_maintenance_events_are_append_only_and_newest_first(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    first = record_phase9_maintenance_event(
        storage,
        operation_key="phase9-research-maintenance",
        activity="evidence-run",
        owner_id="owner-a",
        event_type="LEASE_ACQUIRED",
        status="RUNNING",
        event_time="2026-09-24T10:00:00+00:00",
        details={"lease_until": "2026-09-24T10:30:00+00:00"},
    )
    second = record_phase9_maintenance_event(
        storage,
        operation_key="phase9-research-maintenance",
        activity="evidence-run",
        owner_id="owner-a",
        event_type="RUN_FINISHED",
        status="WAITING_INTERVAL",
        event_time="2026-09-24T10:01:00+00:00",
        details={"steps_attempted": 1},
    )

    assert second.event_id > first.event_id
    events = list_phase9_maintenance_events(storage)
    assert [event.event_type for event in events] == [
        "RUN_FINISHED",
        "LEASE_ACQUIRED",
    ]
    assert events[0].details == {"steps_attempted": 1}


def test_phase9_maintenance_events_filter_by_activity(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for activity in ("source-capture", "research-refresh"):
        record_phase9_maintenance_event(
            storage,
            operation_key="phase9-research-maintenance",
            activity=activity,
            owner_id=f"owner-{activity}",
            event_type="RUN_FINISHED",
            status="COMPLETE",
            event_time="2026-09-24T10:00:00+00:00",
        )

    events = list_phase9_maintenance_events(
        storage,
        activity="research-refresh",
    )
    assert len(events) == 1
    assert events[0].activity == "research-refresh"


def test_phase9_maintenance_events_record_busy_and_recovery_details(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    busy = record_phase9_maintenance_event(
        storage,
        operation_key="phase9-research-maintenance",
        activity="evidence-step",
        owner_id="owner-b",
        event_type="LEASE_BUSY",
        status="BUSY",
        event_time="2026-09-24T10:00:00+00:00",
        details={"existing_owner_id": "owner-a"},
    )
    recovered = record_phase9_maintenance_event(
        storage,
        operation_key="phase9-research-maintenance",
        activity="evidence-run",
        owner_id="owner-c",
        event_type="LEASE_RECOVERED",
        status="RUNNING",
        event_time="2026-09-24T10:31:00+00:00",
        details={"existing_owner_id": "owner-a"},
    )

    assert busy.details["existing_owner_id"] == "owner-a"
    assert recovered.event_type == "LEASE_RECOVERED"


def test_phase9_maintenance_event_validation(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="event_type"):
        record_phase9_maintenance_event(
            storage,
            operation_key="phase9-research-maintenance",
            activity="evidence-run",
            owner_id="owner-a",
            event_type="UNKNOWN",
        )

    with pytest.raises(ValueError, match="limit"):
        list_phase9_maintenance_events(storage, limit=0)


def test_phase9_maintenance_event_rows_are_immutable(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    event = record_phase9_maintenance_event(
        storage,
        operation_key="phase9-research-maintenance",
        activity="evidence-run",
        owner_id="owner-a",
        event_type="LEASE_ACQUIRED",
        status="RUNNING",
        event_time="2026-09-24T10:00:00+00:00",
    )

    with storage.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                """
                UPDATE phase9_maintenance_events
                SET status = 'EDITED'
                WHERE id = ?
                """,
                (event.event_id,),
            )

    with storage.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "DELETE FROM phase9_maintenance_events WHERE id = ?",
                (event.event_id,),
            )


def test_phase9_maintenance_history_cli_returns_recent_events(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    record_phase9_maintenance_event(
        storage,
        operation_key="phase9-research-maintenance",
        activity="evidence-run",
        owner_id="owner-a",
        event_type="RUN_FINISHED",
        status="WAITING_INTERVAL",
        event_time="2026-09-24T10:00:00+00:00",
        details={"steps_attempted": 1},
    )
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pio",
            "phase9-maintenance-history",
            "--activity",
            "evidence-run",
            "--limit",
            "1",
        ],
    )

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["operation_key"] == "phase9-research-maintenance"
    assert payload["activity"] == "evidence-run"
    assert payload["count"] == 1
    assert payload["events"][0]["status"] == "WAITING_INTERVAL"
    assert payload["events"][0]["details"]["steps_attempted"] == 1


def test_phase9_maintenance_lease_is_released_if_journal_write_fails(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    lease = SimpleNamespace(
        operation_key="phase9-research-maintenance",
        owner_id="owner-a",
        acquired=True,
        recovered_stale_lease=False,
        lease_until="2026-09-24T10:30:00+00:00",
        existing_owner_id=None,
        existing_lease_until=None,
    )
    released = []

    monkeypatch.setattr(
        cli,
        "acquire_phase9_operation_lease",
        lambda *args, **kwargs: lease,
    )
    monkeypatch.setattr(
        cli,
        "_record_phase9_lease_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("journal unavailable")
        ),
    )
    monkeypatch.setattr(
        cli,
        "release_phase9_operation_lease",
        lambda *args, **kwargs: released.append(kwargs) or True,
    )

    with pytest.raises(RuntimeError, match="journal unavailable"):
        cli._acquire_phase9_maintenance_lease(
            storage,
            activity="evidence-run",
            lease_seconds=1800,
        )

    assert released == [{
        "operation_key": "phase9-research-maintenance",
        "owner_id": "owner-a",
    }]


def test_phase9_busy_lease_needs_no_release_if_journal_write_fails(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    lease = SimpleNamespace(
        operation_key="phase9-research-maintenance",
        owner_id="owner-b",
        acquired=False,
        recovered_stale_lease=False,
        lease_until=None,
        existing_owner_id="owner-a",
        existing_lease_until="2026-09-24T10:30:00+00:00",
    )
    released = []

    monkeypatch.setattr(
        cli,
        "acquire_phase9_operation_lease",
        lambda *args, **kwargs: lease,
    )
    monkeypatch.setattr(
        cli,
        "_record_phase9_lease_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("journal unavailable")
        ),
    )
    monkeypatch.setattr(
        cli,
        "release_phase9_operation_lease",
        lambda *args, **kwargs: released.append(kwargs) or True,
    )

    with pytest.raises(RuntimeError, match="journal unavailable"):
        cli._acquire_phase9_maintenance_lease(
            storage,
            activity="evidence-step",
            lease_seconds=1800,
        )

    assert released == []
