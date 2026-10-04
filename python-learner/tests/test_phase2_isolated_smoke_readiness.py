from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_isolated_smoke_readiness.py"
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_isolated_smoke_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def unit(name, *, active=False, enabled=False):
    return SimpleNamespace(
        name=name,
        active=active,
        enabled=enabled,
    )


def base_report():
    units = [
        *(unit(name) for name in MODULE.PREFLIGHT.LEGACY_UNITS),
        *(unit(name, active=True) for name in MODULE.STREAM_UNITS),
        unit(
            MODULE.DETECTOR_UNIT,
            active=True,
            enabled=True,
        ),
        unit(MODULE.EVIDENCE_SERVICE),
        unit(MODULE.EVIDENCE_TIMER),
    ]
    return SimpleNamespace(
        runtime_ready=True,
        installed_units_exact=True,
        env_file_regular=True,
        env_keys=(
            SimpleNamespace(name="SOLANA_RPC_URL", configured=True),
            SimpleNamespace(
                name="PIO_PHASE2_POSITION_POOL",
                configured=True,
            ),
        ),
        position_pool_matches_detector_topology=True,
        data_files=(
            SimpleNamespace(
                exists=True,
                regular_file=True,
                symlink=False,
            ),
        ),
        detector_state_valid=True,
        detector_cursors_complete=True,
        unit_states=tuple(units),
    )


def install_report(monkeypatch, report):
    monkeypatch.setattr(
        MODULE.PREFLIGHT,
        "inspect_activation",
        lambda **kwargs: report,
    )


def test_smoke_readiness_accepts_active_detector_with_timer_disabled(
    monkeypatch,
):
    install_report(monkeypatch, base_report())

    report = MODULE.inspect_smoke_readiness()

    assert report.smoke_ready is True
    assert report.streams_active is True
    assert report.detector_active is True
    assert report.detector_enabled is True
    assert report.evidence_timer_inactive is True
    assert report.evidence_timer_disabled is True
    assert report.read_only is True
    assert report.rpc_called is False
    assert report.service_control_performed is False


def test_smoke_readiness_rejects_inactive_stream(monkeypatch):
    source = base_report()
    states = list(source.unit_states)
    target = MODULE.STREAM_UNITS[0]
    states = [
        unit(item.name, active=False, enabled=item.enabled)
        if item.name == target
        else item
        for item in states
    ]
    source.unit_states = tuple(states)
    install_report(monkeypatch, source)

    report = MODULE.inspect_smoke_readiness()

    assert report.smoke_ready is False
    assert report.streams_active is False


def test_smoke_readiness_rejects_legacy_collector_running(monkeypatch):
    source = base_report()
    states = list(source.unit_states)
    legacy = MODULE.PREFLIGHT.LEGACY_UNITS[0]
    states = [
        unit(item.name, active=True, enabled=False)
        if item.name == legacy
        else item
        for item in states
    ]
    source.unit_states = tuple(states)
    install_report(monkeypatch, source)

    report = MODULE.inspect_smoke_readiness()

    assert report.smoke_ready is False
    assert report.legacy_collectors_quiescent is False


def test_smoke_readiness_rejects_enabled_evidence_timer(monkeypatch):
    source = base_report()
    states = list(source.unit_states)
    states = [
        unit(item.name, active=False, enabled=True)
        if item.name == MODULE.EVIDENCE_TIMER
        else item
        for item in states
    ]
    source.unit_states = tuple(states)
    install_report(monkeypatch, source)

    report = MODULE.inspect_smoke_readiness()

    assert report.smoke_ready is False
    assert report.evidence_timer_disabled is False


def test_smoke_readiness_rejects_incomplete_detector_state(monkeypatch):
    source = base_report()
    source.detector_cursors_complete = False
    install_report(monkeypatch, source)

    report = MODULE.inspect_smoke_readiness()

    assert report.smoke_ready is False
    assert report.detector_state_ready is False


def test_smoke_readiness_rejects_env_topology_mismatch(monkeypatch):
    source = base_report()
    source.position_pool_matches_detector_topology = False
    install_report(monkeypatch, source)

    report = MODULE.inspect_smoke_readiness()

    assert report.smoke_ready is False
    assert report.env_ready is False



def test_smoke_readiness_rejects_activation_checker_identity_drift(
    monkeypatch,
):
    install_report(monkeypatch, base_report())
    original = MODULE._preflight_source_identity
    calls = 0

    def drifting_identity():
        nonlocal calls
        calls += 1
        commit, digest = original()
        if calls == 1:
            return commit, digest
        return commit, "0" * len(digest)

    monkeypatch.setattr(
        MODULE,
        "_preflight_source_identity",
        drifting_identity,
    )

    with pytest.raises(
        ValueError,
        match="reviewed activation checker changed",
    ):
        MODULE.inspect_smoke_readiness()


def test_smoke_readiness_does_not_path_reread_activation_source():
    source = Path(MODULE.__file__).read_text(encoding="utf-8")
    inspect_source = source[
        source.index("def inspect_smoke_readiness("):
        source.index("\ndef main() -> None:")
    ]

    assert "PREFLIGHT_TOOL.read_text" not in inspect_source
    assert "PREFLIGHT_TOOL.read_bytes" not in inspect_source
    assert "_preflight_source_identity()" in inspect_source
