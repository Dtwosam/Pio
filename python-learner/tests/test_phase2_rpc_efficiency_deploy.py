import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "apply_phase2_rpc_efficiency_patch.py"
SPEC = importlib.util.spec_from_file_location(
    "apply_phase2_rpc_efficiency_patch",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def git_blob(path: Path) -> str:
    return MODULE.git_blob_sha(path)


def make_tree(tmp_path, name, payload):
    root = tmp_path / name
    target = root / MODULE.TARGET_PATH
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    return root, target


def test_preflight_requires_exact_live_and_target_blobs(tmp_path, monkeypatch):
    live_payload = b"live\n"
    target_payload = b"target\n"
    live_root, live = make_tree(tmp_path, "live", live_payload)
    source_root, source = make_tree(tmp_path, "source", target_payload)

    monkeypatch.setattr(MODULE, "EXPECTED_LIVE_BLOB", git_blob(live))
    monkeypatch.setattr(MODULE, "EXPECTED_TARGET_BLOB", git_blob(source))

    report = MODULE.preflight(
        repository=live_root,
        source_tree=source_root,
    )
    assert report.ready is True
    assert report.status == "READY_UPDATE"
    assert report.service_control_performed is False


def test_preflight_rejects_modified_live_watcher(tmp_path, monkeypatch):
    live_root, live = make_tree(tmp_path, "live", b"unexpected\n")
    source_root, source = make_tree(tmp_path, "source", b"target\n")

    monkeypatch.setattr(MODULE, "EXPECTED_LIVE_BLOB", "0" * 40)
    monkeypatch.setattr(MODULE, "EXPECTED_TARGET_BLOB", git_blob(source))

    report = MODULE.preflight(
        repository=live_root,
        source_tree=source_root,
    )
    assert report.ready is False
    assert report.status == "CONFLICT_LIVE_MODIFIED"


def test_apply_backs_up_and_replaces_only_watcher(tmp_path, monkeypatch):
    live_root, live = make_tree(tmp_path, "live", b"live\n")
    source_root, source = make_tree(tmp_path, "source", b"target\n")
    backup_root = tmp_path / "backups"

    monkeypatch.setattr(MODULE, "EXPECTED_LIVE_BLOB", git_blob(live))
    monkeypatch.setattr(MODULE, "EXPECTED_TARGET_BLOB", git_blob(source))

    report = MODULE.apply_patch(
        repository=live_root,
        source_tree=source_root,
        apply=True,
        backup_dir=backup_root,
    )

    assert report.applied is True
    assert live.read_bytes() == b"target\n"
    assert report.backup_path is not None
    assert Path(report.backup_path).read_bytes() == b"live\n"
    assert report.service_control_performed is False


def test_apply_is_idempotent_when_already_target(tmp_path, monkeypatch):
    live_root, live = make_tree(tmp_path, "live", b"target\n")
    source_root, source = make_tree(tmp_path, "source", b"target\n")

    target_blob = git_blob(source)
    monkeypatch.setattr(MODULE, "EXPECTED_LIVE_BLOB", "0" * 40)
    monkeypatch.setattr(MODULE, "EXPECTED_TARGET_BLOB", target_blob)

    report = MODULE.apply_patch(
        repository=live_root,
        source_tree=source_root,
        apply=True,
        backup_dir=tmp_path / "backups",
    )
    assert report.applied is True
    assert report.status == "ALREADY_TARGET"


def test_preflight_rejects_wrong_source(tmp_path, monkeypatch):
    live_root, live = make_tree(tmp_path, "live", b"live\n")
    source_root, _ = make_tree(tmp_path, "source", b"wrong\n")

    monkeypatch.setattr(MODULE, "EXPECTED_LIVE_BLOB", git_blob(live))
    monkeypatch.setattr(MODULE, "EXPECTED_TARGET_BLOB", "f" * 40)

    report = MODULE.preflight(
        repository=live_root,
        source_tree=source_root,
    )
    assert report.ready is False
    assert report.status == "SOURCE_MISMATCH"
