from pathlib import Path
import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_SERVICE = (
    ROOT
    / "deploy/systemd/pio-phase2-isolated-evidence-cycle.service"
)
PAUSE_SERVICE = (
    ROOT
    / "deploy/systemd/pio-phase2-isolated-rate-limit-pause.service"
)
INSTALLER = ROOT / "deploy/tools/install_phase2_isolated_systemd_units.py"

SPEC = importlib.util.spec_from_file_location(
    "phase2_isolated_systemd_installer_for_onfailure_test",
    INSTALLER,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_failed_evidence_cycle_invokes_local_rate_limit_pause_guard():
    text = EVIDENCE_SERVICE.read_text(encoding="utf-8")

    assert (
        "OnFailure=pio-phase2-isolated-rate-limit-pause.service"
        in text
    )
    assert "EnvironmentFile=/etc/pio/pio.env" in text
    assert "-m meteora_learner.phase2_evidence_cycle" in text


def test_rate_limit_pause_service_is_local_only_and_not_enableable():
    text = PAUSE_SERVICE.read_text(encoding="utf-8")

    assert "Type=oneshot" in text
    assert "TimeoutStartSec=60s" in text
    assert "User=root" in text
    assert "Group=root" in text
    assert "PrivateNetwork=true" in text
    assert "RestrictAddressFamilies=AF_UNIX" in text
    assert (
        "ExecStart=/opt/pio/python-learner/.venv/bin/python "
        "/opt/pio-phase2-runtime/current/deploy/tools/"
        "pause_phase2_isolated_timer.py --apply"
        in text
    )
    assert "ReadOnlyPaths=/opt/pio-phase2-runtime/current" in text

    lowered = text.lower()
    assert "[install]" not in lowered
    assert "environmentfile=" not in lowered
    assert "solana_rpc_url" not in lowered
    assert "pio-phase2-isolated-add-detector" not in lowered
    assert "prestate-stream" not in lowered
    assert "curl " not in lowered
    assert "wget " not in lowered


def test_installer_pins_failure_hook_units_exactly():
    contract = MODULE.UNIT_CONTRACT

    assert contract["pio-phase2-isolated-evidence-cycle.service"] == (
        "3a82fc92e9785f6dcbde8cfd9c3458a71e34c8e3"
    )
    assert contract[
        "pio-phase2-isolated-rate-limit-pause.service"
    ] == "975ff531569cef7aa90c3f7f7055c4df550598ec"

    assert MODULE.git_blob_sha(EVIDENCE_SERVICE) == contract[
        "pio-phase2-isolated-evidence-cycle.service"
    ]
    assert MODULE.git_blob_sha(PAUSE_SERVICE) == contract[
        "pio-phase2-isolated-rate-limit-pause.service"
    ]
