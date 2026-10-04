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
    (current.parent / "release").mkdir()
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



def test_systemd_installer_executes_captured_runtime_checker_bytes():
    source = TOOL.read_text(encoding="utf-8")

    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "compile(captured.encoded" in source
    assert "exec(code, module.__dict__)" in source
    assert "spec.loader.exec_module(module)" not in source
    assert ".read_bytes(" not in source
    assert "shutil.copy2(" not in source


def test_systemd_installer_runtime_checker_matches_reviewed_head():
    commit, checker_sha = MODULE._runtime_checker_source_identity()

    assert len(commit) in {40, 64}
    assert len(checker_sha) == 64


def test_systemd_preflight_rejects_same_content_source_replacement(
    tmp_path,
    monkeypatch,
):
    source = unit_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    bypass_runtime(monkeypatch, tmp_path)
    name = next(iter(MODULE.UNIT_CONTRACT))
    target = source / "deploy" / "systemd" / name

    real_snapshot = MODULE._snapshot_unit
    replaced = False

    def snapshot_and_replace(**kwargs):
        nonlocal replaced
        snapshot = real_snapshot(**kwargs)
        if kwargs["name"] == name and not replaced:
            replacement = target.with_name(f"{name}.replacement")
            replacement.write_bytes(target.read_bytes())
            replacement.replace(target)
            replaced = True
        return snapshot

    monkeypatch.setattr(MODULE, "_snapshot_unit", snapshot_and_replace)

    with pytest.raises(
        ValueError,
        match="reviewed source unit .* path changed after preflight",
    ):
        MODULE.inspect_install(
            source_tree=source,
            runtime_root=tmp_path / "runtime",
            destination=destination,
        )


def test_systemd_apply_aborts_if_source_changes_after_preflight(
    tmp_path,
    monkeypatch,
):
    source = unit_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    bypass_runtime(monkeypatch, tmp_path)
    name = next(iter(MODULE.UNIT_CONTRACT))
    source_unit = source / "deploy" / "systemd" / name

    real_inspect = MODULE._inspect_install_snapshot

    def inspect_then_replace(**kwargs):
        report, snapshots = real_inspect(**kwargs)
        source_unit.write_text(
            source_unit.read_text(encoding="utf-8") + "\n# late drift\n",
            encoding="utf-8",
        )
        return report, snapshots

    monkeypatch.setattr(
        MODULE,
        "_inspect_install_snapshot",
        inspect_then_replace,
    )

    with pytest.raises(
        ValueError,
        match="reviewed source unit .* path changed after preflight",
    ):
        MODULE.install_units(
            source_tree=source,
            runtime_root=tmp_path / "runtime",
            destination=destination,
            apply=True,
        )

    assert list(destination.iterdir()) == []


def test_systemd_apply_does_not_clobber_target_that_appears_after_preflight(
    tmp_path,
    monkeypatch,
):
    source = unit_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    bypass_runtime(monkeypatch, tmp_path)
    name = next(iter(MODULE.UNIT_CONTRACT))
    target = destination / name

    real_inspect = MODULE._inspect_install_snapshot

    def inspect_then_create(**kwargs):
        report, snapshots = real_inspect(**kwargs)
        target.write_text("[Unit]\nDescription=concurrent owner\n")
        return report, snapshots

    monkeypatch.setattr(
        MODULE,
        "_inspect_install_snapshot",
        inspect_then_create,
    )

    with pytest.raises(
        ValueError,
        match="installed unit .* path changed after preflight",
    ):
        MODULE.install_units(
            source_tree=source,
            runtime_root=tmp_path / "runtime",
            destination=destination,
            apply=True,
        )

    assert target.read_text(encoding="utf-8") == (
        "[Unit]\nDescription=concurrent owner\n"
    )
