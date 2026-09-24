import pytest

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
