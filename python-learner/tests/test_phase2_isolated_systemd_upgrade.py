from pathlib import Path
import importlib.util
import shutil
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/upgrade_phase2_isolated_systemd_units.py"
SPEC = importlib.util.spec_from_file_location(
    "upgrade_phase2_isolated_systemd_units",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


OLD_BYTES = {
    "pio-phase2-isolated-evidence-cycle.service": (
        b"[Unit]\nDescription=old evidence service\n"
    ),
    "pio-phase2-isolated-evidence-cycle.timer": (
        b"[Unit]\nDescription=old evidence timer\n"
    ),
}


def configure_contract(monkeypatch):
    transitions = {}
    for name, old_bytes in OLD_BYTES.items():
        target = (
            ROOT / "deploy" / "systemd" / name
        ).read_bytes()
        transitions[name] = (
            MODULE.git_blob_sha_bytes(old_bytes),
            MODULE.git_blob_sha_bytes(target),
        )
    monkeypatch.setattr(MODULE, "UNIT_TRANSITIONS", transitions)


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    directory = source / "deploy" / "systemd"
    directory.mkdir(parents=True)
    for name in OLD_BYTES:
        shutil.copy2(
            ROOT / "deploy" / "systemd" / name,
            directory / name,
        )
    return source


def test_upgrade_preflight_accepts_exact_previous_units(tmp_path, monkeypatch):
    configure_contract(monkeypatch)
    source = make_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    for name, payload in OLD_BYTES.items():
        (destination / name).write_bytes(payload)

    report = MODULE.inspect_upgrade(
        source_tree=source,
        destination=destination,
    )

    assert report.ready is True
    assert report.applied is False
    assert {row.status for row in report.units} == {"READY_UPDATE"}
    assert report.daemon_reload_performed is False
    assert report.service_control_performed is False
    assert report.rpc_called is False


def test_upgrade_applies_exact_targets_with_backups(tmp_path, monkeypatch):
    configure_contract(monkeypatch)
    source = make_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    for name, payload in OLD_BYTES.items():
        path = destination / name
        path.write_bytes(payload)
        path.chmod(0o644)

    backup_dir = tmp_path / "backups"
    report = MODULE.upgrade_units(
        source_tree=source,
        destination=destination,
        backup_dir=backup_dir,
        apply=True,
    )

    assert report.applied is True
    assert report.files_updated == 2
    assert report.backup_root is not None
    assert {row.status for row in report.units} == {"ALREADY_TARGET"}
    for name, old_bytes in OLD_BYTES.items():
        assert (destination / name).read_bytes() == (
            source / "deploy" / "systemd" / name
        ).read_bytes()
        assert stat.S_IMODE((destination / name).stat().st_mode) == 0o644
        assert (Path(report.backup_root) / name).read_bytes() == old_bytes
    assert report.daemon_reload_performed is False
    assert report.service_control_performed is False


def test_upgrade_allows_missing_peer_for_create_only_installer(tmp_path, monkeypatch):
    configure_contract(monkeypatch)
    source = make_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    first = next(iter(OLD_BYTES))
    (destination / first).write_bytes(OLD_BYTES[first])

    report = MODULE.upgrade_units(
        source_tree=source,
        destination=destination,
        backup_dir=tmp_path / "backups",
        apply=True,
    )

    statuses = {row.name: row.status for row in report.units}
    assert statuses[first] == "ALREADY_TARGET"
    assert set(statuses.values()) == {"ALREADY_TARGET", "NOT_INSTALLED"}
    assert report.files_updated == 1


def test_upgrade_refuses_unknown_local_override(tmp_path, monkeypatch):
    configure_contract(monkeypatch)
    source = make_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    for name, payload in OLD_BYTES.items():
        (destination / name).write_bytes(payload)
    first = next(iter(OLD_BYTES))
    (destination / first).write_text("[Unit]\nDescription=local override\n")

    report = MODULE.inspect_upgrade(
        source_tree=source,
        destination=destination,
    )

    statuses = {row.name: row.status for row in report.units}
    assert statuses[first] == "CONFLICT_MODIFIED"
    assert report.ready is False
    with pytest.raises(ValueError, match="preflight is not ready"):
        MODULE.upgrade_units(
            source_tree=source,
            destination=destination,
            backup_dir=tmp_path / "backups",
            apply=True,
        )


def test_upgrade_refuses_symlinked_installed_unit(tmp_path, monkeypatch):
    configure_contract(monkeypatch)
    source = make_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    first = next(iter(OLD_BYTES))
    outside = tmp_path / "outside"
    outside.write_bytes(OLD_BYTES[first])
    (destination / first).symlink_to(outside)

    report = MODULE.inspect_upgrade(
        source_tree=source,
        destination=destination,
    )

    statuses = {row.name: row.status for row in report.units}
    assert statuses[first] == "CONFLICT_INSTALLED_PATH"
    assert report.ready is False


def test_upgrade_refuses_live_production_checkout_as_source(tmp_path):
    destination = tmp_path / "systemd"
    destination.mkdir()
    with pytest.raises(ValueError, match="must not be /opt/pio"):
        MODULE.inspect_upgrade(
            source_tree="/opt/pio",
            destination=destination,
        )
