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
