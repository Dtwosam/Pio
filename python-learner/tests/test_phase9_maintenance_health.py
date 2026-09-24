import json
import sys

import pytest

from meteora_learner import cli
from meteora_learner.phase9_maintenance_health import (
    evaluate_phase9_maintenance_health,
)
from meteora_learner.phase9_maintenance_history import (
    record_phase9_maintenance_event,
)
from meteora_learner.phase9_operation_lease import (
    acquire_phase9_operation_lease,
)
from meteora_learner.storage import Storage


KEY = "phase9-research-maintenance"
NOW = "2026-09-24T12:00:00+00:00"


def finish(storage, status, *, event_time="2026-09-24T11:30:00+00:00"):
    record_phase9_maintenance_event(
        storage,
        operation_key=KEY,
        activity="evidence-run",
        owner_id="owner-a",
        event_type="RUN_FINISHED",
        status=status,
        event_time=event_time,
    )


def test_phase9_maintenance_health_accepts_recent_complete_run(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    finish(storage, "COMPLETE")

    report = evaluate_phase9_maintenance_health(storage, as_of=NOW)

    assert report.healthy is True
    assert report.attention_required is False
    assert report.status == "HEALTHY"
    assert report.latest_terminal_status == "COMPLETE"
    assert report.seconds_since_latest_event == 1800


def test_phase9_maintenance_health_surfaces_manual_blocker_without_failure(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    finish(storage, "MANUAL_REQUIRED")

    report = evaluate_phase9_maintenance_health(storage, as_of=NOW)

    assert report.healthy is True
    assert report.attention_required is True
    assert report.status == "BLOCKED_MANUAL"
    assert "operator inputs" in report.reasons[0]


def test_phase9_maintenance_health_treats_interval_wait_as_healthy(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    finish(storage, "WAITING_INTERVAL")

    report = evaluate_phase9_maintenance_health(storage, as_of=NOW)

    assert report.healthy is True
    assert report.attention_required is False
    assert report.status == "WAITING_INTERVAL"


def test_phase9_maintenance_health_fails_recent_failed_run(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    finish(storage, "FAILED")

    report = evaluate_phase9_maintenance_health(storage, as_of=NOW)

    assert report.healthy is False
    assert report.status == "DEGRADED"
    assert report.recent_failure_events == 1


def test_phase9_maintenance_health_fails_stale_history(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    finish(
        storage,
        "COMPLETE",
        event_time="2026-09-24T08:00:00+00:00",
    )

    report = evaluate_phase9_maintenance_health(
        storage,
        as_of=NOW,
        max_event_age_seconds=10_800,
    )

    assert report.healthy is False
    assert report.status == "STALE"


def test_phase9_maintenance_health_accepts_active_recent_lease(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    lease = acquire_phase9_operation_lease(
        storage,
        operation_key=KEY,
        lease_seconds=1800,
        owner_id="owner-a",
        as_of="2026-09-24T11:50:00+00:00",
    )
    record_phase9_maintenance_event(
        storage,
        operation_key=KEY,
        activity="evidence-run",
        owner_id=lease.owner_id,
        event_type="LEASE_ACQUIRED",
        status="RUNNING",
        event_time="2026-09-24T11:50:00+00:00",
    )

    report = evaluate_phase9_maintenance_health(storage, as_of=NOW)

    assert report.healthy is True
    assert report.status == "RUNNING"
    assert report.lease_active is True


def test_phase9_maintenance_health_fails_expired_orphan_lease(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    lease = acquire_phase9_operation_lease(
        storage,
        operation_key=KEY,
        lease_seconds=300,
        owner_id="owner-a",
        as_of="2026-09-24T11:00:00+00:00",
    )
    record_phase9_maintenance_event(
        storage,
        operation_key=KEY,
        activity="evidence-run",
        owner_id=lease.owner_id,
        event_type="LEASE_ACQUIRED",
        status="RUNNING",
        event_time="2026-09-24T11:00:00+00:00",
    )

    report = evaluate_phase9_maintenance_health(storage, as_of=NOW)

    assert report.healthy is False
    assert report.status == "EXPIRED_LEASE"
    assert report.lease_expired is True


def test_phase9_maintenance_health_requires_history(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = evaluate_phase9_maintenance_health(storage, as_of=NOW)

    assert report.healthy is False
    assert report.status == "NO_HISTORY"
    assert report.attention_required is True


def test_phase9_maintenance_health_cli_require_healthy(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    finish(storage, "COMPLETE")
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pio",
            "phase9-maintenance-health",
            "--as-of",
            NOW,
            "--require-healthy",
        ],
    )

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["healthy"] is True
    assert payload["status"] == "HEALTHY"


def test_phase9_maintenance_health_cli_exits_nonzero_when_unhealthy(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    finish(storage, "FAILED")
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pio",
            "phase9-maintenance-health",
            "--as-of",
            NOW,
            "--require-healthy",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["healthy"] is False
    assert payload["status"] == "DEGRADED"


def test_phase9_maintenance_health_ignores_future_events_at_cutoff(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    finish(
        storage,
        "COMPLETE",
        event_time="2026-09-24T10:30:00+00:00",
    )
    finish(
        storage,
        "FAILED",
        event_time="2026-09-24T12:30:00+00:00",
    )

    report = evaluate_phase9_maintenance_health(
        storage,
        as_of="2026-09-24T11:00:00+00:00",
    )

    assert report.healthy is True
    assert report.status == "HEALTHY"
    assert report.latest_terminal_status == "COMPLETE"
    assert report.recent_failure_events == 0
