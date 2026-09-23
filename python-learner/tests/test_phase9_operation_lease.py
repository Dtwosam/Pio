import meteora_learner.phase9_operation_lease as lease_module
from meteora_learner.phase9_operation_lease import (
    acquire_phase9_operation_lease,
    phase9_operation_lease_status,
    release_phase9_operation_lease,
)
from meteora_learner.storage import Storage


def test_phase9_operation_lease_blocks_overlapping_owner(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    first = acquire_phase9_operation_lease(
        storage,
        operation_key="source-capture",
        lease_seconds=600,
        owner_id="owner-a",
        as_of="2026-09-23T20:00:00+00:00",
    )
    second = acquire_phase9_operation_lease(
        storage,
        operation_key="source-capture",
        lease_seconds=600,
        owner_id="owner-b",
        as_of="2026-09-23T20:05:00+00:00",
    )

    assert first.acquired is True
    assert first.recovered_stale_lease is False
    assert second.acquired is False
    assert second.existing_owner_id == "owner-a"
    assert second.existing_lease_until == (
        "2026-09-23T20:10:00+00:00"
    )


def test_phase9_operation_lease_recovers_expired_lease(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    acquire_phase9_operation_lease(
        storage,
        operation_key="research-refresh",
        lease_seconds=300,
        owner_id="owner-a",
        as_of="2026-09-23T20:00:00+00:00",
    )
    recovered = acquire_phase9_operation_lease(
        storage,
        operation_key="research-refresh",
        lease_seconds=300,
        owner_id="owner-b",
        as_of="2026-09-23T20:06:00+00:00",
    )

    assert recovered.acquired is True
    assert recovered.recovered_stale_lease is True
    assert recovered.existing_owner_id == "owner-a"
    assert recovered.existing_lease_until == (
        "2026-09-23T20:05:00+00:00"
    )
    assert recovered.lease_until == "2026-09-23T20:11:00+00:00"


def test_phase9_operation_lease_release_requires_owner(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    lease = acquire_phase9_operation_lease(
        storage,
        operation_key="source-capture",
        lease_seconds=600,
        owner_id="owner-a",
        as_of="2026-09-23T20:00:00+00:00",
    )

    assert release_phase9_operation_lease(
        storage,
        operation_key="source-capture",
        owner_id="owner-b",
    ) is False
    assert release_phase9_operation_lease(
        storage,
        operation_key="source-capture",
        owner_id=lease.owner_id,
    ) is True
    assert release_phase9_operation_lease(
        storage,
        operation_key="source-capture",
        owner_id=lease.owner_id,
    ) is False


def test_phase9_operation_lease_validates_inputs(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    try:
        acquire_phase9_operation_lease(
            storage,
            operation_key="",
            lease_seconds=1,
        )
    except ValueError as exc:
        assert "operation_key" in str(exc)
    else:
        raise AssertionError("expected missing operation key failure")

    try:
        acquire_phase9_operation_lease(
            storage,
            operation_key="x",
            lease_seconds=0,
        )
    except ValueError as exc:
        assert "lease_seconds" in str(exc)
    else:
        raise AssertionError("expected invalid lease duration failure")


def test_phase9_operation_lease_status_reports_idle_active_and_expired(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")

    idle = phase9_operation_lease_status(
        storage,
        operation_key="phase9-research-maintenance",
        as_of="2026-09-23T20:00:00+00:00",
    )
    assert idle.exists is False
    assert idle.active is False
    assert idle.expired is False
    assert idle.remaining_seconds is None

    acquire_phase9_operation_lease(
        storage,
        operation_key="phase9-research-maintenance",
        lease_seconds=600,
        owner_id="owner-a",
        as_of="2026-09-23T20:00:00+00:00",
    )

    active = phase9_operation_lease_status(
        storage,
        operation_key="phase9-research-maintenance",
        as_of="2026-09-23T20:05:00+00:00",
    )
    assert active.exists is True
    assert active.active is True
    assert active.expired is False
    assert active.owner_id == "owner-a"
    assert active.remaining_seconds == 300

    expired = phase9_operation_lease_status(
        storage,
        operation_key="phase9-research-maintenance",
        as_of="2026-09-23T20:11:00+00:00",
    )
    assert expired.exists is True
    assert expired.active is False
    assert expired.expired is True
    assert expired.remaining_seconds == 0
