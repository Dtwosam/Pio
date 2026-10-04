from pathlib import Path
import importlib.util
import json
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
    assert report.upgrade_needed is True
    assert report.installer_needed is False
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
    assert report.upgrade_needed is False
    assert report.installer_needed is True
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



def test_upgrade_reports_already_current_without_mutation(tmp_path, monkeypatch):
    configure_contract(monkeypatch)
    source = make_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    for name in OLD_BYTES:
        shutil.copy2(
            source / "deploy" / "systemd" / name,
            destination / name,
        )

    report = MODULE.inspect_upgrade(
        source_tree=source,
        destination=destination,
    )

    assert report.ready is True
    assert report.upgrade_needed is False
    assert report.installer_needed is False
    assert {row.status for row in report.units} == {"ALREADY_TARGET"}


def test_upgrade_rejects_symlinked_source_root(tmp_path):
    source = tmp_path / "real-source"
    (source / "deploy" / "systemd").mkdir(parents=True)
    linked = tmp_path / "linked-source"
    linked.symlink_to(source, target_is_directory=True)
    destination = tmp_path / "systemd"
    destination.mkdir()

    with pytest.raises(ValueError, match="source must not be a symlink"):
        MODULE.inspect_upgrade(
            source_tree=linked,
            destination=destination,
        )


def test_upgrade_rejects_symlinked_destination_root(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "real-systemd"
    destination.mkdir()
    linked = tmp_path / "linked-systemd"
    linked.symlink_to(destination, target_is_directory=True)

    with pytest.raises(ValueError, match="destination must not be a symlink"):
        MODULE.inspect_upgrade(
            source_tree=source,
            destination=linked,
        )


def test_upgrade_tool_uses_no_follow_descriptor_reads():
    source = TOOL.read_text(encoding="utf-8")

    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "os.open(" in source



def test_upgrade_restores_current_unit_when_publish_verification_fails(
    tmp_path,
    monkeypatch,
):
    configure_contract(monkeypatch)
    source = make_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    for name, payload in OLD_BYTES.items():
        path = destination / name
        path.write_bytes(payload)
        path.chmod(0o644)

    first = next(iter(OLD_BYTES))
    real_write = MODULE._write_target
    injected = False

    def replace_then_fail(**kwargs):
        nonlocal injected
        if kwargs["destination"].name == first and not injected:
            injected = True
            real_write(**kwargs)
            raise ValueError("injected post-publish verification failure")
        return real_write(**kwargs)

    monkeypatch.setattr(MODULE, "_write_target", replace_then_fail)

    with pytest.raises(MODULE.UnitUpgradeApplyError) as excinfo:
        MODULE.upgrade_units(
            source_tree=source,
            destination=destination,
            backup_dir=tmp_path / "backups",
            apply=True,
        )

    report = excinfo.value.report
    assert report.applied is False
    assert report.failure_step == f"UPDATE:{first}"
    assert report.rollback_performed is True
    assert report.rollback_succeeded is True
    assert report.files_updated == 0
    assert report.backup_root is not None

    for name, payload in OLD_BYTES.items():
        assert (destination / name).read_bytes() == payload



def test_upgrade_reports_uncertain_rollback_without_leaking_failure_text(
    tmp_path,
    monkeypatch,
):
    configure_contract(monkeypatch)
    source = make_source(tmp_path)
    destination = tmp_path / "systemd"
    destination.mkdir()
    for name, payload in OLD_BYTES.items():
        path = destination / name
        path.write_bytes(payload)
        path.chmod(0o644)

    first = next(iter(OLD_BYTES))
    real_write = MODULE._write_target
    injected = False
    secret = "https://rpc.invalid/?api-key=secret"

    def replace_then_fail(**kwargs):
        nonlocal injected
        if kwargs["destination"].name == first and not injected:
            injected = True
            real_write(**kwargs)
            raise ValueError(f"injected failure at {secret}")
        return real_write(**kwargs)

    def rollback_fails(**kwargs):
        raise ValueError(f"rollback diagnostic at {secret}")

    monkeypatch.setattr(MODULE, "_write_target", replace_then_fail)
    monkeypatch.setattr(MODULE, "_restore_backup", rollback_fails)

    with pytest.raises(MODULE.UnitUpgradeApplyError) as excinfo:
        MODULE.upgrade_units(
            source_tree=source,
            destination=destination,
            backup_dir=tmp_path / "backups",
            apply=True,
        )

    report = excinfo.value.report
    assert report.applied is False
    assert report.failure_step == f"UPDATE:{first}"
    assert report.rollback_performed is True
    assert report.rollback_succeeded is False
    assert report.files_updated == 1
    assert secret not in str(excinfo.value)
    assert secret not in json.dumps(report.to_record())
    assert (destination / first).read_bytes() == (
        source / "deploy" / "systemd" / first
    ).read_bytes()


def test_upgrade_main_prints_structured_json_for_apply_failure(
    tmp_path,
    monkeypatch,
    capsys,
):
    source = tmp_path / "source"
    source.mkdir()
    report = MODULE.UnitUpgradeReport(
        source_tree=str(source),
        destination=str(tmp_path / "systemd"),
        backup_root=str(tmp_path / "backups" / "stamp"),
        ready=True,
        upgrade_needed=True,
        installer_needed=False,
        applied=False,
        units=(),
        files_updated=0,
        failure_step="UPDATE:unit.service",
        rollback_performed=True,
        rollback_succeeded=True,
        daemon_reload_performed=False,
        service_control_performed=False,
        rpc_called=False,
    )

    def fail(**kwargs):
        raise MODULE.UnitUpgradeApplyError(report)

    monkeypatch.setattr(MODULE, "upgrade_units", fail)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(TOOL),
            "--source-tree",
            str(source),
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        MODULE.main()

    assert excinfo.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False
    assert payload["failure_step"] == "UPDATE:unit.service"
    assert payload["rollback_performed"] is True
    assert payload["rollback_succeeded"] is True
    assert payload["rpc_called"] is False
    assert payload["service_control_performed"] is False


def test_upgrade_accepts_additional_reviewed_predecessor(
    tmp_path,
    monkeypatch,
):
    name = "pio-phase2-isolated-evidence-cycle.service"
    target_bytes = b"[Unit]\nDescription=new target\n"
    previous_bytes = b"[Unit]\nDescription=previous target\n"
    older_bytes = b"[Unit]\nDescription=older reviewed target\n"

    target_blob = MODULE.git_blob_sha_bytes(target_bytes)
    previous_blob = MODULE.git_blob_sha_bytes(previous_bytes)
    older_blob = MODULE.git_blob_sha_bytes(older_bytes)

    monkeypatch.setattr(
        MODULE,
        "UNIT_TRANSITIONS",
        {name: (previous_blob, target_blob)},
    )
    monkeypatch.setattr(
        MODULE,
        "ADDITIONAL_PREVIOUS_BLOBS",
        {name: (older_blob,)},
    )

    source = tmp_path / "source"
    source_unit = source / "deploy" / "systemd" / name
    source_unit.parent.mkdir(parents=True)
    source_unit.write_bytes(target_bytes)

    destination = tmp_path / "systemd"
    destination.mkdir()
    (destination / name).write_bytes(older_bytes)

    report = MODULE.inspect_upgrade(
        source_tree=source,
        destination=destination,
    )

    assert report.ready is True
    assert report.upgrade_needed is True
    assert report.installer_needed is False
    assert len(report.units) == 1
    assert report.units[0].status == "READY_UPDATE"
