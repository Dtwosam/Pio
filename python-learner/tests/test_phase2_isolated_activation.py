from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_isolated_activation.py"
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_isolated_activation",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


PRIMARY_POOL = "54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ"


def install_unit_tree(tmp_path: Path) -> Path:
    destination = tmp_path / "systemd"
    destination.mkdir()
    for name in MODULE.INSTALL.UNIT_CONTRACT:
        shutil.copy2(
            ROOT / "deploy" / "systemd" / name,
            destination / name,
        )
    return destination


def data_tree(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    (root / "pio.db").write_text("db", encoding="utf-8")
    (root / "phase2-prestate-cache.db").write_text("cache", encoding="utf-8")
    (root / "phase2-add-detector-state.json").write_text(
        json.dumps(
            {
                "cursors": {
                    "54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ": "sig-a",
                    "DQ9weJhfiU4iL5LUoeshDrm5KxDHCMiSbnnKJz7buMcf": "sig-b",
                },
                "processed": [],
                "pending": {},
            }
        ),
        encoding="utf-8",
    )
    return root


def env_file(tmp_path: Path, *, pool: str = PRIMARY_POOL) -> Path:
    path = tmp_path / "pio.env"
    path.write_text(
        "SOLANA_RPC_URL=https://rpc.invalid/?api-key=secret\n"
        f"PIO_PHASE2_POSITION_POOL={pool}\n",
        encoding="utf-8",
    )
    return path


def bypass_install(monkeypatch, tmp_path: Path, destination: Path):
    release = tmp_path / "runtime" / "releases" / "pin"
    release.mkdir(parents=True)
    current = release.parent.parent / "current"
    current.symlink_to(Path("releases") / "pin")

    monkeypatch.setattr(
        MODULE.INSTALL,
        "_validate_runtime",
        lambda _root: current,
    )

    rows = tuple(
        SimpleNamespace(name=name, status="ALREADY_TARGET")
        for name in MODULE.INSTALL.UNIT_CONTRACT
    )
    monkeypatch.setattr(
        MODULE.INSTALL,
        "inspect_install",
        lambda **kwargs: SimpleNamespace(
            runtime_ready=True,
            units=rows,
        ),
    )
    return current


def systemctl_runner(*, active=(), enabled=()):
    active = set(active)
    enabled = set(enabled)

    def runner(command, **kwargs):
        action, unit = command[1], command[2]
        if action == "is-active":
            value = "active" if unit in active else "inactive"
            code = 0 if unit in active else 3
        elif action == "is-enabled":
            value = "enabled" if unit in enabled else "disabled"
            code = 0 if unit in enabled else 1
        else:
            raise AssertionError(f"unexpected systemctl action: {action}")
        return subprocess.CompletedProcess(
            command,
            code,
            stdout=value + "\n",
            stderr="",
        )

    return runner


def ready_inputs(tmp_path, monkeypatch):
    destination = install_unit_tree(tmp_path)
    bypass_install(monkeypatch, tmp_path, destination)
    return {
        "runtime_root": tmp_path / "runtime",
        "unit_destination": destination,
        "env_file": env_file(tmp_path),
        "data_root": data_tree(tmp_path),
    }


def test_activation_preflight_ready_is_read_only(tmp_path, monkeypatch):
    args = ready_inputs(tmp_path, monkeypatch)

    report = MODULE.inspect_activation(
        **args,
        runner=systemctl_runner(),
    )

    assert report.activation_ready is True
    assert report.runtime_ready is True
    assert report.installed_units_exact is True
    assert report.position_pool_matches_detector_topology is True
    assert report.detector_pool_count == 2
    assert report.detector_state_valid is True
    assert report.detector_cursor_pools == 2
    assert report.detector_cursors_complete is True
    assert all(item.configured for item in report.env_keys)
    assert all(item.ready for item in report.unit_states)
    assert report.read_only is True
    assert report.rpc_called is False
    assert report.daemon_reload_performed is False
    assert report.service_control_performed is False
    encoded = str(report.to_record())
    assert "api-key=secret" not in encoded
    assert "https://rpc.invalid" not in encoded


def test_activation_preflight_rejects_active_legacy_collector(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)

    report = MODULE.inspect_activation(
        **args,
        runner=systemctl_runner(
            active={"pio-phase2-add-detector.service"},
        ),
    )

    assert report.activation_ready is False
    state = next(
        item
        for item in report.unit_states
        if item.name == "pio-phase2-add-detector.service"
    )
    assert state.active is True
    assert state.ready is False


def test_activation_preflight_rejects_enabled_new_detector(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)

    report = MODULE.inspect_activation(
        **args,
        runner=systemctl_runner(
            enabled={"pio-phase2-isolated-add-detector.service"},
        ),
    )

    assert report.activation_ready is False


def test_activation_preflight_rejects_missing_required_env_key(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)
    Path(args["env_file"]).write_text(
        f"PIO_PHASE2_POSITION_POOL={PRIMARY_POOL}\n",
        encoding="utf-8",
    )

    report = MODULE.inspect_activation(
        **args,
        runner=systemctl_runner(),
    )

    assert report.activation_ready is False
    configured = {
        item.name: item.configured for item in report.env_keys
    }
    assert configured["SOLANA_RPC_URL"] is False


def test_activation_preflight_rejects_pool_outside_detector_topology(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)
    args["env_file"] = env_file(
        tmp_path,
        pool="DifferentPool111111111111111111111111111111",
    )

    report = MODULE.inspect_activation(
        **args,
        runner=systemctl_runner(),
    )

    assert report.activation_ready is False
    assert report.position_pool_matches_detector_topology is False


def test_activation_preflight_rejects_missing_state_file(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)
    (Path(args["data_root"]) / "phase2-add-detector-state.json").unlink()

    report = MODULE.inspect_activation(
        **args,
        runner=systemctl_runner(),
    )

    assert report.activation_ready is False
    detector_state = next(
        item
        for item in report.data_files
        if item.name == "phase2-add-detector-state.json"
    )
    assert detector_state.exists is False



def test_activation_preflight_rejects_incomplete_detector_cursors(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)
    state = Path(args["data_root"]) / "phase2-add-detector-state.json"
    state.write_text(
        json.dumps(
            {
                "cursors": {
                    "54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ": "sig-a",
                    "DQ9weJhfiU4iL5LUoeshDrm5KxDHCMiSbnnKJz7buMcf": None,
                },
                "processed": [],
                "pending": {},
            }
        ),
        encoding="utf-8",
    )

    report = MODULE.inspect_activation(
        **args,
        runner=systemctl_runner(),
    )

    assert report.activation_ready is False
    assert report.detector_state_valid is True
    assert report.detector_cursor_pools == 1
    assert report.detector_cursors_complete is False


def test_activation_preflight_rejects_malformed_detector_state(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)
    state = Path(args["data_root"]) / "phase2-add-detector-state.json"
    state.write_text("{broken", encoding="utf-8")

    report = MODULE.inspect_activation(
        **args,
        runner=systemctl_runner(),
    )

    assert report.activation_ready is False
    assert report.detector_state_valid is False
    assert report.detector_cursors_complete is False



def test_activation_preflight_blocks_overlapping_legacy_rpc_collectors(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)
    overlapping = {
        "pio-phase2-position-observer.service",
        "pio-phase2-position-observer.timer",
        "pio-phase2-evidence-cycle.service",
        "pio-phase2-evidence-cycle.timer",
    }

    assert overlapping.issubset(set(MODULE.LEGACY_UNITS))

    for unit_name in sorted(overlapping):
        report = MODULE.inspect_activation(
            **args,
            runner=systemctl_runner(enabled={unit_name}),
        )
        state = next(
            item for item in report.unit_states
            if item.name == unit_name
        )
        assert report.activation_ready is False
        assert state.enabled is True
        assert state.must_be_disabled is True
        assert state.ready is False


def test_activation_does_not_disable_independent_research_quote_density():
    assert "pio-phase2-research-quotes.service" not in MODULE.LEGACY_UNITS
    assert "pio-phase2-research-quotes.timer" not in MODULE.LEGACY_UNITS



def test_activation_preflight_rejects_same_content_env_replacement(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)
    env = Path(args["env_file"])
    base_runner = systemctl_runner()
    replaced = False

    def runner(command, **kwargs):
        nonlocal replaced
        if not replaced:
            replacement = env.with_name("pio.env.replacement")
            replacement.write_bytes(env.read_bytes())
            replacement.replace(env)
            replaced = True
        return base_runner(command, **kwargs)

    with pytest.raises(
        ValueError,
        match="Phase-2 environment file path changed",
    ):
        MODULE.inspect_activation(**args, runner=runner)


def test_activation_preflight_rejects_detector_state_replacement(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)
    state = (
        Path(args["data_root"])
        / "phase2-add-detector-state.json"
    )
    base_runner = systemctl_runner()
    replaced = False

    def runner(command, **kwargs):
        nonlocal replaced
        if not replaced:
            replacement = state.with_name(f"{state.name}.replacement")
            replacement.write_bytes(state.read_bytes())
            replacement.replace(state)
            replaced = True
        return base_runner(command, **kwargs)

    with pytest.raises(
        ValueError,
        match="Phase-2 data file phase2-add-detector-state.json "
        "path changed",
    ):
        MODULE.inspect_activation(**args, runner=runner)


def test_activation_preflight_rejects_runtime_current_retarget(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)
    current = Path(args["runtime_root"]) / "current"
    alternate = (
        Path(args["runtime_root"])
        / "releases"
        / "alternate"
    )
    alternate.mkdir(parents=True)
    base_runner = systemctl_runner()
    changed = False

    def runner(command, **kwargs):
        nonlocal changed
        if not changed:
            current.unlink()
            current.symlink_to(Path("releases") / "alternate")
            changed = True
        return base_runner(command, **kwargs)

    with pytest.raises(
        ValueError,
        match="isolated runtime current path changed",
    ):
        MODULE.inspect_activation(**args, runner=runner)


def test_activation_preflight_requires_stable_systemctl_snapshot(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)
    original = MODULE._all_unit_states
    calls = 0

    def unstable(*, runner):
        nonlocal calls
        calls += 1
        rows = original(runner=runner)
        if calls == 1:
            return rows
        first, *rest = rows
        changed = MODULE.ActivationUnitState(
            name=first.name,
            active_state="active",
            enabled_state=first.enabled_state,
            active=True,
            enabled=first.enabled,
            must_be_disabled=first.must_be_disabled,
            ready=False,
        )
        return (changed, *rest)

    monkeypatch.setattr(MODULE, "_all_unit_states", unstable)

    with pytest.raises(
        ValueError,
        match="systemd unit state changed",
    ):
        MODULE.inspect_activation(
            **args,
            runner=systemctl_runner(),
        )


def test_activation_preflight_rejects_installer_identity_drift(
    tmp_path,
    monkeypatch,
):
    args = ready_inputs(tmp_path, monkeypatch)
    original = MODULE._install_source_identity
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
        "_install_source_identity",
        drifting_identity,
    )

    with pytest.raises(
        ValueError,
        match="reviewed systemd installer changed",
    ):
        MODULE.inspect_activation(
            **args,
            runner=systemctl_runner(),
        )
