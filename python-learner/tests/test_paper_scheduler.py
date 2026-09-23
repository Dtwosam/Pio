from dataclasses import replace

from meteora_learner.paper_scheduler import (
    paper_scheduler_state,
    run_scheduled_paper_tick,
)
from meteora_learner.paper_tick import PaperTickReport
from meteora_learner.storage import Storage


NOW = "2026-09-23T10:02:31+00:00"


def fake_tick(status="COMPLETE"):
    def run(storage, **kwargs):
        return PaperTickReport(
            tick_id=kwargs["tick_id"],
            account_id=kwargs["account_id"],
            observed_at=kwargs["observed_at"],
            status=status,
            reused_existing_tick=False,
            market={},
            chain={},
            quote_refresh={},
            supervisor={},
            error=None,
        )
    return run


def test_scheduler_uses_deterministic_bucket_tick_and_releases_lease(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    def run(storage, **kwargs):
        calls.append(kwargs)
        return fake_tick()(storage, **kwargs)

    result = run_scheduled_paper_tick(
        storage,
        account_id="paper",
        interval_seconds=300,
        lease_seconds=240,
        owner_id="worker-a",
        as_of=NOW,
        tick_runner=run,
    )

    assert result.status == "COMPLETE"
    assert result.lease_acquired is True
    assert result.tick_id == "paper-schedule:paper:1790157600"
    assert calls[0]["tick_id"] == result.tick_id
    assert calls[0]["observed_at"] == NOW
    state = paper_scheduler_state(storage, account_id="paper")
    assert state.owner_id is None
    assert state.lease_until is None
    assert state.last_tick_id == result.tick_id
    assert state.last_status == "COMPLETE"
    assert state.total_ticks == 1
    assert state.consecutive_failures == 0


def test_scheduler_refuses_overlap_while_lease_is_live(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_scheduler_state(
                account_id, owner_id, lease_until, heartbeat_at,
                last_tick_id, last_started_at, last_status,
                consecutive_failures, total_ticks, updated_at
            ) VALUES (
                'paper', 'other-worker', '2026-09-23T10:06:00+00:00',
                '2026-09-23T10:01:00+00:00', 'old',
                '2026-09-23T10:01:00+00:00', 'RUNNING', 0, 2,
                '2026-09-23T10:01:00+00:00'
            )
            """
        )

    called = []
    result = run_scheduled_paper_tick(
        storage,
        account_id="paper",
        interval_seconds=300,
        lease_seconds=240,
        owner_id="worker-a",
        as_of=NOW,
        tick_runner=lambda *args, **kwargs: called.append(True),
    )

    assert result.status == "BUSY"
    assert result.lease_acquired is False
    assert called == []
    assert result.scheduler_state.owner_id == "other-worker"
    assert result.scheduler_state.total_ticks == 2


def test_scheduler_recovers_expired_lease_and_updates_health(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_scheduler_state(
                account_id, owner_id, lease_until, heartbeat_at,
                last_tick_id, last_started_at, last_status,
                consecutive_failures, total_ticks, updated_at
            ) VALUES (
                'paper', 'dead-worker', '2026-09-23T10:00:00+00:00',
                '2026-09-23T09:59:00+00:00', 'old',
                '2026-09-23T09:59:00+00:00', 'RUNNING', 2, 4,
                '2026-09-23T09:59:00+00:00'
            )
            """
        )

    result = run_scheduled_paper_tick(
        storage,
        account_id="paper",
        interval_seconds=300,
        lease_seconds=240,
        owner_id="worker-b",
        as_of=NOW,
        tick_runner=fake_tick("FAILED"),
    )

    assert result.recovered_stale_lease is True
    assert result.status == "FAILED"
    assert result.scheduler_state.owner_id is None
    assert result.scheduler_state.total_ticks == 5
    assert result.scheduler_state.consecutive_failures == 3


def test_scheduler_resets_failure_streak_after_success(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    first = run_scheduled_paper_tick(
        storage,
        account_id="paper",
        interval_seconds=300,
        lease_seconds=240,
        as_of=NOW,
        tick_runner=fake_tick("FAILED"),
    )
    assert first.scheduler_state.consecutive_failures == 1

    second = run_scheduled_paper_tick(
        storage,
        account_id="paper",
        interval_seconds=300,
        lease_seconds=240,
        as_of="2026-09-23T10:07:31+00:00",
        tick_runner=fake_tick("COMPLETE"),
    )
    assert second.scheduler_state.total_ticks == 2
    assert second.scheduler_state.consecutive_failures == 0
