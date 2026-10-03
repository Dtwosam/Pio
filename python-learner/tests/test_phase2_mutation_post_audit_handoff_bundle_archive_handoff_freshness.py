from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy/tools/check_phase2_mutation_post_audit_handoff_bundle_archive_handoff_freshness.py"
)
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_archive_handoff_freshness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def good_snapshot(**overrides):
    values = {
        "snapshot_path": "/evidence/archive-handoff.snapshot.json",
        "snapshot_sha256": "1" * 64,
        "handoff_snapshot_verified": True,
        "historical_handoff_only": True,
        "current_lifecycle_rechecked": False,
        "authorizes_next_action": False,
        "requires_fresh_separate_mutation_authorization": True,
        "read_only": True,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
        "mutation_executed": False,
        "archive_sha256": "a" * 64,
        "source_bundle_sha256": "b" * 64,
        "recorded_current_state": "TIMER_ACTIVATION_READY",
        "recorded_current_next_action": "ACTIVATE_EVIDENCE_TIMER",
        "recorded_current_next_tool": "activate_phase2_isolated_timer.py",
        "recorded_current_next_mutation_flag": "--apply",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def good_archive(**overrides):
    values = {
        "archive_path": "/moved/phase2-handoff.tar",
        "archive_sha256": "a" * 64,
        "archive_size": 4096,
        "archive_verified": True,
        "source_bundle_sha256": "b" * 64,
        "source_bundle_verified": True,
        "deterministic_metadata_valid": True,
        "temporary_extraction_performed": True,
        "authorizes_next_action": False,
        "requires_fresh_separate_mutation_authorization": True,
        "production_file_modified": False,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
        "mutation_executed": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def install(monkeypatch, *, snapshot=None, archive=None):
    snapshot_report = snapshot or good_snapshot()
    archive_report = archive or good_archive()
    monkeypatch.setattr(
        MODULE.SNAPSHOT,
        "verify_phase2_portable_archive_current_handoff_snapshot",
        lambda **kwargs: snapshot_report,
    )
    monkeypatch.setattr(
        MODULE.ARCHIVE,
        "verify_phase2_portable_bundle_archive",
        lambda **kwargs: archive_report,
    )
    monkeypatch.setattr(
        MODULE,
        "_capture_snapshot_identity",
        lambda snapshot_path: (
            Path(str(snapshot_path)),
            str(snapshot_report.snapshot_sha256),
            object(),
        ),
    )
    monkeypatch.setattr(
        MODULE,
        "_capture_archive_identity",
        lambda archive_path: (
            Path(str(archive_path)),
            str(archive_report.archive_sha256),
            int(archive_report.archive_size),
            object(),
        ),
    )
    monkeypatch.setattr(
        MODULE.SNAPSHOT,
        "_assert_snapshot_path_stable",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        MODULE.ARCHIVE,
        "_assert_archive_path_stable",
        lambda *args, **kwargs: None,
    )


def test_fresh_reverification_matches_archive_by_identity_not_path(monkeypatch):
    install(monkeypatch)

    report = MODULE.freshly_reverify_phase2_portable_archive_handoff(
        snapshot_path="/evidence/archive-handoff.snapshot.json",
        archive_path="/moved/phase2-handoff.tar",
        repository_root=ROOT,
    )

    assert report.snapshot_verified is True
    assert report.archive_verified is True
    assert report.archive_identity_matches is True
    assert report.source_bundle_identity_matches is True
    assert report.archive_paths_ignored_for_identity is True
    assert report.archive_reverified is True
    assert report.fresh_reverification_verified is True
    assert report.recorded_current_state == "TIMER_ACTIVATION_READY"
    assert report.recorded_current_next_action == "ACTIVATE_EVIDENCE_TIMER"
    assert report.current_lifecycle_rechecked is False
    assert report.authorizes_next_action is False
    assert report.requires_fresh_separate_mutation_authorization is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False
    assert report.mutation_executed is False


def test_fresh_reverification_detects_archive_byte_identity_drift(monkeypatch):
    install(
        monkeypatch,
        archive=good_archive(archive_sha256="c" * 64),
    )

    report = MODULE.freshly_reverify_phase2_portable_archive_handoff(
        snapshot_path="/evidence/archive-handoff.snapshot.json",
        archive_path="/moved/phase2-handoff.tar",
        repository_root=ROOT,
    )

    assert report.archive_identity_matches is False
    assert report.source_bundle_identity_matches is True
    assert report.fresh_reverification_verified is False


def test_fresh_reverification_detects_source_bundle_identity_drift(monkeypatch):
    install(
        monkeypatch,
        archive=good_archive(source_bundle_sha256="c" * 64),
    )

    report = MODULE.freshly_reverify_phase2_portable_archive_handoff(
        snapshot_path="/evidence/archive-handoff.snapshot.json",
        archive_path="/moved/phase2-handoff.tar",
        repository_root=ROOT,
    )

    assert report.archive_identity_matches is True
    assert report.source_bundle_identity_matches is False
    assert report.fresh_reverification_verified is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("handoff_snapshot_verified", False),
        ("historical_handoff_only", False),
        ("current_lifecycle_rechecked", True),
        ("authorizes_next_action", True),
        ("requires_fresh_separate_mutation_authorization", False),
        ("read_only", False),
        ("rpc_called", True),
        ("database_write_performed", True),
        ("service_control_performed", True),
        ("mutation_executed", True),
    ],
)
def test_fresh_reverification_rejects_snapshot_boundary_crossing(
    monkeypatch,
    field,
    value,
):
    install(monkeypatch, snapshot=good_snapshot(**{field: value}))

    with pytest.raises(ValueError, match="historical evidence boundary"):
        MODULE.freshly_reverify_phase2_portable_archive_handoff(
            snapshot_path="/evidence/archive-handoff.snapshot.json",
            archive_path="/moved/phase2-handoff.tar",
            repository_root=ROOT,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("archive_verified", False),
        ("source_bundle_verified", False),
        ("deterministic_metadata_valid", False),
        ("temporary_extraction_performed", False),
        ("authorizes_next_action", True),
        ("requires_fresh_separate_mutation_authorization", False),
        ("production_file_modified", True),
        ("rpc_called", True),
        ("database_write_performed", True),
        ("service_control_performed", True),
        ("mutation_executed", True),
    ],
)
def test_fresh_reverification_rejects_archive_boundary_crossing(
    monkeypatch,
    field,
    value,
):
    install(monkeypatch, archive=good_archive(**{field: value}))

    with pytest.raises(ValueError, match="historical evidence boundary"):
        MODULE.freshly_reverify_phase2_portable_archive_handoff(
            snapshot_path="/evidence/archive-handoff.snapshot.json",
            archive_path="/moved/phase2-handoff.tar",
            repository_root=ROOT,
        )


def test_fresh_reverification_rejects_secret_bearing_archive_path(monkeypatch):
    install(monkeypatch)

    with pytest.raises(ValueError, match="sensitive"):
        MODULE.freshly_reverify_phase2_portable_archive_handoff(
            snapshot_path="/evidence/archive-handoff.snapshot.json",
            archive_path="/tmp/api-key=secret.tar",
            repository_root=ROOT,
        )



def test_fresh_reverification_rejects_snapshot_child_identity_drift(
    monkeypatch,
):
    install(monkeypatch, snapshot=good_snapshot(snapshot_sha256="2" * 64))
    monkeypatch.setattr(
        MODULE,
        "_capture_snapshot_identity",
        lambda snapshot_path: (
            Path(str(snapshot_path)),
            "1" * 64,
            object(),
        ),
    )

    with pytest.raises(ValueError, match="orchestrator byte snapshot"):
        MODULE.freshly_reverify_phase2_portable_archive_handoff(
            snapshot_path="/evidence/archive-handoff.snapshot.json",
            archive_path="/moved/phase2-handoff.tar",
            repository_root=ROOT,
        )


@pytest.mark.parametrize(
    "archive_overrides",
    [
        {"archive_sha256": "c" * 64},
        {"archive_size": 8192},
    ],
)
def test_fresh_reverification_rejects_archive_child_identity_drift(
    monkeypatch,
    archive_overrides,
):
    install(monkeypatch, archive=good_archive(**archive_overrides))
    monkeypatch.setattr(
        MODULE,
        "_capture_archive_identity",
        lambda archive_path: (
            Path(str(archive_path)),
            "a" * 64,
            4096,
            object(),
        ),
    )

    with pytest.raises(ValueError, match="orchestrator byte snapshot"):
        MODULE.freshly_reverify_phase2_portable_archive_handoff(
            snapshot_path="/evidence/archive-handoff.snapshot.json",
            archive_path="/moved/phase2-handoff.tar",
            repository_root=ROOT,
        )


def test_fresh_reverification_rejects_path_replacement_during_children(
    tmp_path,
    monkeypatch,
):
    snapshot_path = tmp_path / "archive-handoff.snapshot.json"
    snapshot_bytes = b'{"snapshot":"reviewed"}'
    snapshot_path.write_bytes(snapshot_bytes)
    snapshot_path.chmod(0o600)

    archive_path = tmp_path / "phase2-handoff.tar"
    archive_bytes = b"reviewed archive bytes"
    archive_path.write_bytes(archive_bytes)
    archive_path.chmod(0o600)

    original_snapshot_capture = MODULE._capture_snapshot_identity
    original_archive_capture = MODULE._capture_archive_identity
    original_snapshot_assert = MODULE.SNAPSHOT._assert_snapshot_path_stable
    original_archive_assert = MODULE.ARCHIVE._assert_archive_path_stable

    snapshot_report = good_snapshot(
        snapshot_path=str(snapshot_path),
        snapshot_sha256=hashlib.sha256(snapshot_bytes).hexdigest(),
        archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
    )
    archive_report = good_archive(
        archive_path=str(archive_path),
        archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        archive_size=len(archive_bytes),
    )
    install(
        monkeypatch,
        snapshot=snapshot_report,
        archive=archive_report,
    )
    monkeypatch.setattr(
        MODULE,
        "_capture_snapshot_identity",
        original_snapshot_capture,
    )
    monkeypatch.setattr(
        MODULE,
        "_capture_archive_identity",
        original_archive_capture,
    )
    monkeypatch.setattr(
        MODULE.SNAPSHOT,
        "_assert_snapshot_path_stable",
        original_snapshot_assert,
    )
    monkeypatch.setattr(
        MODULE.ARCHIVE,
        "_assert_archive_path_stable",
        original_archive_assert,
    )

    def replace_snapshot(**kwargs):
        replacement = tmp_path / "replacement.snapshot.json"
        replacement.write_bytes(b'{"snapshot":"replaced"}')
        replacement.chmod(0o600)
        os.replace(replacement, snapshot_path)
        return archive_report

    monkeypatch.setattr(
        MODULE.ARCHIVE,
        "verify_phase2_portable_bundle_archive",
        replace_snapshot,
    )

    with pytest.raises(ValueError, match="snapshot path changed"):
        MODULE.freshly_reverify_phase2_portable_archive_handoff(
            snapshot_path=snapshot_path,
            archive_path=archive_path,
            repository_root=ROOT,
        )
