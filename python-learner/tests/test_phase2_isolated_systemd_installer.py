from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/install_phase2_isolated_systemd_units.py"
SPEC = importlib.util.spec_from_file_location(
    "install_phase2_isolated_systemd_units",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def unit_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    directory = source / "deploy" / "systemd"
    directory.mkdir(parents=True)
    for name in MODULE.UNIT_CONTRACT:
        shutil.copy2(ROOT / "deploy" / "systemd" / name, directory / name)
    return source


def bypass_runtime(monkeypatch, tmp_path: Path):
    current = tmp_path / "runtime" / "current"
    current.parent.mkdir(parents=True)
    current.symlink_to("release")
    monkeypatch.setattr(
        MODULE,
        "_validate_runtime",
        lambda _root: current,
    )
    return current


def test_systemd_installer_preflight_is_non_mutating(tmp_path, monkeypatch):
    source = unit_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    current = bypass_runtime(monkeypatch, tmp_path)

    report = MODULE.install_units(
        source_tree=source,
        runtime_root=tmp_path / "runtime",
        destination=destination,
    )

    assert report.runtime_current == str(current)
    assert report.ready is True
    assert report.applied is False
    assert {row.status for row in report.units} == {"READY_CREATE"}
    assert list(destination.iterdir()) == []
    assert report.daemon_reload_performed is False
    assert report.service_control_performed is False
    assert report.services_enabled is False
    assert report.services_started is False
    assert report.rpc_called is False


def test_systemd_installer_creates_exact_units_without_control_actions(
    tmp_path,
    monkeypatch,
):
    source = unit_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    bypass_runtime(monkeypatch, tmp_path)

    report = MODULE.install_units(
        source_tree=source,
        runtime_root=tmp_path / "runtime",
        destination=destination,
        apply=True,
    )

    assert report.applied is True
    assert {row.status for row in report.units} == {"ALREADY_TARGET"}
    for name, expected in MODULE.UNIT_CONTRACT.items():
        target = destination / name
        assert MODULE.git_blob_sha(target) == expected
        assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert report.daemon_reload_performed is False
    assert report.service_control_performed is False
    assert report.services_enabled is False
    assert report.services_started is False


def test_systemd_installer_is_idempotent(tmp_path, monkeypatch):
    source = unit_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    bypass_runtime(monkeypatch, tmp_path)

    MODULE.install_units(
        source_tree=source,
        runtime_root=tmp_path / "runtime",
        destination=destination,
        apply=True,
    )
    second = MODULE.install_units(
        source_tree=source,
        runtime_root=tmp_path / "runtime",
        destination=destination,
        apply=True,
    )

    assert second.applied is True
    assert {row.status for row in second.units} == {"ALREADY_TARGET"}


def test_systemd_installer_refuses_modified_existing_unit(
    tmp_path,
    monkeypatch,
):
    source = unit_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    bypass_runtime(monkeypatch, tmp_path)

    name = next(iter(MODULE.UNIT_CONTRACT))
    (destination / name).write_text("[Unit]\nDescription=local override\n")

    report = MODULE.install_units(
        source_tree=source,
        runtime_root=tmp_path / "runtime",
        destination=destination,
    )

    statuses = {row.name: row.status for row in report.units}
    assert statuses[name] == "CONFLICT_MODIFIED"
    assert report.ready is False

    with pytest.raises(ValueError, match="preflight is not ready"):
        MODULE.install_units(
            source_tree=source,
            runtime_root=tmp_path / "runtime",
            destination=destination,
            apply=True,
        )
    assert (destination / name).read_text() == (
        "[Unit]\nDescription=local override\n"
    )


def test_systemd_installer_refuses_source_drift(tmp_path, monkeypatch):
    source = unit_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    bypass_runtime(monkeypatch, tmp_path)

    name = next(iter(MODULE.UNIT_CONTRACT))
    with (source / "deploy" / "systemd" / name).open("a") as handle:
        handle.write("\n# drift\n")

    report = MODULE.install_units(
        source_tree=source,
        runtime_root=tmp_path / "runtime",
        destination=destination,
    )

    statuses = {row.name: row.status for row in report.units}
    assert statuses[name] == "SOURCE_DRIFT"
    assert report.ready is False


def test_systemd_installer_refuses_symlinked_target(tmp_path, monkeypatch):
    source = unit_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    bypass_runtime(monkeypatch, tmp_path)

    name = next(iter(MODULE.UNIT_CONTRACT))
    real = tmp_path / "outside.service"
    real.write_text("outside")
    (destination / name).symlink_to(real)

    report = MODULE.install_units(
        source_tree=source,
        runtime_root=tmp_path / "runtime",
        destination=destination,
    )

    statuses = {row.name: row.status for row in report.units}
    assert statuses[name] == "CONFLICT_SYMLINK"
    assert report.ready is False
