from io import StringIO
import json
import subprocess
from types import SimpleNamespace

import pytest

from meteora_learner.phase2_event_prestate import (
    _await_subscription_ready,
    _notification_stream,
    _start_watch_process,
    capture_pool_snapshot,
    run_event_prestate_session,
)
from meteora_learner.phase2_rpc_guard import Phase2RpcRateLimited


POOL = "pool"


def snapshot(slot, *, active_bin_id=5):
    return {
        "pool_address": POOL,
        "capture_slot_start": slot,
        "capture_slot_end": slot,
        "active_bin_id": active_bin_id,
        "bin_arrays": [
            {
                "address": "array",
                "index": 0,
                "lower_bin_id": 0,
                "upper_bin_id": 9,
                "bins": [
                    {
                        "bin_id": active_bin_id,
                        "price": "1",
                        "amount_x": "10",
                        "amount_y": "20",
                        "liquidity_supply": "30",
                        "fee_amount_x_per_token_stored": "0",
                        "fee_amount_y_per_token_stored": "0",
                        "reward_per_token_stored": ["0", "0"],
                    }
                ],
            }
        ],
    }


def cache_rows(path):
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            """
            SELECT capture_slot_end, active_bin_id, bin_array_address
            FROM prestate_snapshots
            ORDER BY id
            """
        ).fetchall()
    finally:
        conn.close()


def test_event_prestate_saves_baseline_and_refresh_on_change(tmp_path):
    cache = tmp_path / "prestate.db"
    captures = iter((snapshot(100), snapshot(101)))

    report = run_event_prestate_session(
        cache_path=cache,
        pool_address=POOL,
        notifications=(
            {"account_address": POOL, "slot": 101},
        ),
        capture_snapshot=lambda: next(captures),
        observed_at=iter(
            (
                "2026-10-02T12:00:00+00:00",
                "2026-10-02T12:00:01+00:00",
            )
        ).__next__,
    )

    assert report.baseline_slot == 100
    assert report.notifications_seen == 1
    assert report.refreshes_attempted == 1
    assert report.refreshes_saved == 1
    assert report.cache_rows_saved == 2
    assert report.polling_loop_used is False
    assert cache_rows(cache) == [
        (100, 5, "array"),
        (101, 5, "array"),
    ]


def test_event_prestate_ignores_notification_already_covered_by_baseline(
    tmp_path,
):
    cache = tmp_path / "prestate.db"
    calls = 0

    def capture():
        nonlocal calls
        calls += 1
        return snapshot(100)

    report = run_event_prestate_session(
        cache_path=cache,
        pool_address=POOL,
        notifications=(
            {"account_address": POOL, "slot": 99},
            {"account_address": POOL, "slot": 100},
        ),
        capture_snapshot=capture,
        observed_at=lambda: "2026-10-02T12:00:00+00:00",
    )

    assert calls == 1
    assert report.stale_notifications == 2
    assert report.refreshes_attempted == 0
    assert cache_rows(cache) == [(100, 5, "array")]


def test_event_prestate_coalesces_notifications_covered_by_one_refresh(
    tmp_path,
):
    cache = tmp_path / "prestate.db"
    captures = iter((snapshot(100), snapshot(102)))
    calls = 0

    def capture():
        nonlocal calls
        calls += 1
        return next(captures)

    report = run_event_prestate_session(
        cache_path=cache,
        pool_address=POOL,
        notifications=(
            {"account_address": POOL, "slot": 101},
            {"account_address": POOL, "slot": 102},
        ),
        capture_snapshot=capture,
        observed_at=lambda: "2026-10-02T12:00:00+00:00",
    )

    assert calls == 2
    assert report.notifications_seen == 2
    assert report.stale_notifications == 1
    assert report.refreshes_attempted == 1
    assert cache_rows(cache) == [
        (100, 5, "array"),
        (102, 5, "array"),
    ]


def test_event_prestate_fails_closed_when_refresh_is_behind_notification(
    tmp_path,
):
    cache = tmp_path / "prestate.db"
    captures = iter((snapshot(100), snapshot(104)))

    with pytest.raises(
        ValueError,
        match="behind account notification",
    ):
        run_event_prestate_session(
            cache_path=cache,
            pool_address=POOL,
            notifications=(
                {"account_address": POOL, "slot": 105},
            ),
            capture_snapshot=lambda: next(captures),
            observed_at=lambda: "2026-10-02T12:00:00+00:00",
        )

    assert cache_rows(cache) == [(100, 5, "array")]


def test_event_prestate_preserves_add_detector_candidate_contract(tmp_path):
    cache = tmp_path / "prestate.db"
    captures = iter(
        (
            snapshot(100, active_bin_id=5),
            snapshot(110, active_bin_id=6),
        )
    )

    run_event_prestate_session(
        cache_path=cache,
        pool_address=POOL,
        notifications=(
            {"account_address": POOL, "slot": 110},
        ),
        capture_snapshot=lambda: next(captures),
        observed_at=iter(
            (
                "2026-10-02T12:00:00+00:00",
                "2026-10-02T12:00:10+00:00",
            )
        ).__next__,
    )

    import sqlite3

    conn = sqlite3.connect(cache)
    try:
        row = conn.execute(
            """
            SELECT capture_slot_end, active_bin_id
            FROM prestate_snapshots
            WHERE pool_address = ?
              AND capture_slot_end < ?
              AND active_bin_id = ?
            ORDER BY capture_slot_end DESC, id DESC
            LIMIT 1
            """,
            (POOL, 110, 5),
        ).fetchone()
    finally:
        conn.close()

    assert row == (100, 5)


def test_event_prestate_rejects_wrong_notification_address(tmp_path):
    cache = tmp_path / "prestate.db"

    with pytest.raises(ValueError, match="address mismatch"):
        run_event_prestate_session(
            cache_path=cache,
            pool_address=POOL,
            notifications=(
                {"account_address": "other", "slot": 101},
            ),
            capture_snapshot=lambda: snapshot(100),
            observed_at=lambda: "2026-10-02T12:00:00+00:00",
        )


def test_pool_capture_uses_secret_safe_rpc_rate_limit_signal():
    secret = "https://rpc.invalid/?api-key=secret"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            f"HTTP 429 Too Many Requests at {secret}",
        )

    with pytest.raises(Phase2RpcRateLimited) as excinfo:
        capture_pool_snapshot(
            executor_path="/executor",
            pool_address=POOL,
            runner=runner,
        )

    assert str(excinfo.value) == "RPC_RATE_LIMITED"
    assert secret not in str(excinfo.value)



def test_watch_handshake_precedes_account_change_stream():
    process = SimpleNamespace(
        stdout=StringIO(
            '{"kind":"SUBSCRIBED","account_address":"pool"}\n'
            '{"kind":"ACCOUNT_CHANGE","account_address":"pool","slot":101}\n'
        )
    )

    _await_subscription_ready(process, pool_address=POOL)
    assert list(_notification_stream(process)) == [
        {
            "kind": "ACCOUNT_CHANGE",
            "account_address": POOL,
            "slot": 101,
        }
    ]


def test_watch_handshake_rejects_wrong_subscription_address():
    process = SimpleNamespace(
        stdout=StringIO(
            '{"kind":"SUBSCRIBED","account_address":"other"}\n'
        )
    )

    with pytest.raises(ValueError, match="subscription address mismatch"):
        _await_subscription_ready(process, pool_address=POOL)


def test_watch_process_uses_dedicated_binary_without_rpc_url_in_argv(
    monkeypatch,
):
    seen = {}

    def fake_popen(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return SimpleNamespace(stdout=StringIO(""))

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    _start_watch_process(
        watch_executor_path="/runtime/pio-phase2-account-watch",
        pool_address=POOL,
        max_notifications=7,
    )

    assert seen["command"] == [
        "/runtime/pio-phase2-account-watch",
        POOL,
        "7",
    ]
    assert all("RPC" not in value for value in seen["command"])
