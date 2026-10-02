from pathlib import Path

from meteora_learner.phase2_stream_supervisor import (
    build_event_stream_command,
    restart_backoff_seconds,
    supervise_prestate_stream,
)


def test_restart_backoff_escalates_and_caps():
    assert restart_backoff_seconds(0) == 0.0
    assert restart_backoff_seconds(1) == 5.0
    assert restart_backoff_seconds(2) == 10.0
    assert restart_backoff_seconds(6) == 160.0
    assert restart_backoff_seconds(7) == 300.0
    assert restart_backoff_seconds(100) == 300.0


def test_supervisor_backs_off_only_after_child_exit():
    runs = iter(((2, 1.0), (2, 2.0), (2, 3.0)))
    sleeps = []

    report = supervise_prestate_stream(
        ("python", "-m", "meteora_learner.phase2_event_prestate"),
        max_runs=3,
        run_once=lambda _command: next(runs),
        sleeper=sleeps.append,
    )

    assert sleeps == [5.0, 10.0]
    assert report.runs_completed == 3
    assert report.consecutive_failures == 3
    assert report.failure_only_backoff is True
    assert report.healthy_stream_throttled is False


def test_stable_stream_resets_failure_streak():
    runs = iter(((2, 1.0), (2, 2.0), (2, 301.0), (2, 1.0)))
    sleeps = []

    report = supervise_prestate_stream(
        ("stream",),
        stable_reset_seconds=300.0,
        max_runs=4,
        run_once=lambda _command: next(runs),
        sleeper=sleeps.append,
    )

    assert sleeps == [5.0, 10.0, 5.0]
    assert report.consecutive_failures == 2


def test_event_stream_command_contains_no_rpc_credentials(monkeypatch):
    monkeypatch.setenv(
        "SOLANA_RPC_URL",
        "https://rpc.invalid/?api-key=secret",
    )

    command = build_event_stream_command(
        python_executable="/venv/bin/python",
        executor_path="/runtime/meteora-executor",
        watch_executor_path="/runtime/pio-phase2-account-watch",
        cache_database="/data/phase2-prestate-cache.db",
        pool_address="pool",
    )

    encoded = " ".join(command)
    assert "secret" not in encoded
    assert "SOLANA_RPC_URL" not in encoded
    assert command[-2:] == ("--pool", "pool")


def test_isolated_stream_service_uses_failure_only_supervisor():
    root = Path(__file__).resolve().parents[2]
    service = (
        root
        / "deploy/systemd/pio-phase2-isolated-prestate-stream.service"
    ).read_text(encoding="utf-8")

    assert "-m meteora_learner.phase2_stream_supervisor" in service
    assert "-m meteora_learner.phase2_event_prestate" not in service
    assert "Restart=on-failure" in service



def test_template_stream_service_pins_instance_as_pool_without_polling():
    root = Path(__file__).resolve().parents[2]
    service = (
        root
        / "deploy/systemd/pio-phase2-isolated-prestate-stream@.service"
    ).read_text(encoding="utf-8")

    assert "-m meteora_learner.phase2_stream_supervisor" in service
    assert "--pool %i" in service
    assert "Restart=on-failure" in service
    assert "Conflicts=pio-phase2-prestate-watch.service" in service
    assert "SOLANA_RPC_URL=" not in service
    assert "http://" not in service
    assert "https://" not in service
