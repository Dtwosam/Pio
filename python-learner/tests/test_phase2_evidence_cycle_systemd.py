from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "deploy" / "systemd" / "pio-phase2-evidence-cycle.service"
TIMER = ROOT / "deploy" / "systemd" / "pio-phase2-evidence-cycle.timer"
README = ROOT / "deploy" / "systemd" / "README-phase2-evidence-cycle.md"


def test_phase2_evidence_cycle_service_is_read_only_and_env_configured():
    text = SERVICE.read_text(encoding="utf-8")

    assert "Type=oneshot" in text
    assert "User=pio" in text
    assert "EnvironmentFile=/etc/pio/pio.env" in text
    assert "-m meteora_learner.phase2_evidence_cycle" in text
    assert "--persist-progress" in text
    assert "NoNewPrivileges=true" in text
    assert "ProtectSystem=strict" in text
    assert "ReadWritePaths=/opt/pio/data" in text

    lowered = text.lower()
    assert "systemctl" not in lowered
    assert "pio-phase2-add-detector" not in lowered
    assert "pio-phase2-prestate-watch" not in lowered
    assert "git " not in lowered
    assert "$rpc_url" not in lowered
    assert "$solana_rpc_url" not in lowered


def test_phase2_evidence_cycle_timer_matches_neutral_position_cadence():
    text = TIMER.read_text(encoding="utf-8")

    assert "OnBootSec=10min" in text
    assert "OnUnitActiveSec=15min" in text
    assert "Persistent=true" in text
    assert "Unit=pio-phase2-evidence-cycle.service" in text
    assert "WantedBy=timers.target" in text


def test_phase2_evidence_cycle_deployment_notes_keep_timer_inactive_by_default():
    text = README.read_text(encoding="utf-8")

    assert "Nothing in this directory enables" in text
    assert "Do not enable `pio-phase2-position-observer.timer`" in text
    assert "active detector batch must have committed" in text
    assert "Issue #6" in text
    assert "selective deployment preflight" in text
