from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVICE = (
    ROOT
    / "deploy"
    / "systemd"
    / "pio-phase2-isolated-evidence-cycle.service"
)
TIMER = (
    ROOT
    / "deploy"
    / "systemd"
    / "pio-phase2-isolated-evidence-cycle.timer"
)


def test_isolated_evidence_service_conflicts_with_legacy_position_observer():
    text = SERVICE.read_text(encoding="utf-8")

    assert (
        "Conflicts=pio-phase2-position-observer.service "
        "pio-phase2-evidence-cycle.service"
    ) in text
    assert "OnFailure=pio-phase2-isolated-rate-limit-pause.service" in text
    assert (
        "/opt/pio-phase2-runtime/current/"
        "rust-executor/target/release/meteora-executor"
    ) in text


def test_isolated_evidence_timer_conflicts_with_legacy_position_timer():
    text = TIMER.read_text(encoding="utf-8")

    assert (
        "Conflicts=pio-phase2-position-observer.timer "
        "pio-phase2-evidence-cycle.timer"
    ) in text
    assert "OnUnitActiveSec=15min" in text
    assert "Unit=pio-phase2-isolated-evidence-cycle.service" in text


def test_collector_exclusion_does_not_reduce_isolated_cycle_capacity():
    service = SERVICE.read_text(encoding="utf-8")
    timer = TIMER.read_text(encoding="utf-8")

    assert "--max-positions-per-run" not in service
    assert "--max-reinspection-tasks" not in service
    assert "--max-prestate-tasks" not in service
    assert "OnUnitActiveSec=15min" in timer
