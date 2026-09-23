from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "deploy" / "systemd"


def test_phase9_source_capture_service_is_read_only_and_bounded():
    service = (
        SYSTEMD / "pio-phase9-source-capture.service"
    ).read_text(encoding="utf-8")

    assert "phase9-source-capture-run" in service
    assert "--history-min-observation-interval-seconds 3600" in service
    assert "SOLANA_RPC_URL" not in service
    assert "--require-automatic-ready" not in service
    assert "live-submit" not in service
    assert "sign" not in service.lower()
    assert "private key" not in service.lower()
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "ProtectHome=true" in service
    assert "ReadWritePaths=/opt/pio/data" in service


def test_phase9_source_capture_timer_stays_above_minimum_interval():
    timer = (
        SYSTEMD / "pio-phase9-source-capture.timer"
    ).read_text(encoding="utf-8")

    assert "OnUnitActiveSec=70min" in timer
    assert "Persistent=true" in timer
    assert "Unit=pio-phase9-source-capture.service" in timer


def test_phase9_research_refresh_service_cannot_promote_or_execute():
    service = (
        SYSTEMD / "pio-phase9-research-refresh.service"
    ).read_text(encoding="utf-8")

    assert "phase9-research-refresh-run" in service
    assert "phase9-validate" not in service
    assert "persist-ready" not in service
    assert "phase9-source-capture-run" not in service
    assert "SOLANA_RPC_URL" not in service
    assert "live-submit" not in service
    assert "sign" not in service.lower()
    assert "private key" not in service.lower()
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "ProtectHome=true" in service
    assert "ReadWritePaths=/opt/pio/data" in service


def test_phase9_research_refresh_timer_runs_after_source_capture_offset():
    timer = (
        SYSTEMD / "pio-phase9-research-refresh.timer"
    ).read_text(encoding="utf-8")
    service = (
        SYSTEMD / "pio-phase9-research-refresh.service"
    ).read_text(encoding="utf-8")

    assert "OnBootSec=25min" in timer
    assert "OnUnitActiveSec=70min" in timer
    assert "Persistent=true" in timer
    assert "Unit=pio-phase9-research-refresh.service" in timer
    assert "After=pio-phase9-source-capture.service" in service
