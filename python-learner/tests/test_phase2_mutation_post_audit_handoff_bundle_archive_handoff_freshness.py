from __future__ import annotations

import importlib.util
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
    monkeypatch.setattr(
        MODULE.SNAPSHOT,
        "verify_phase2_portable_archive_current_handoff_snapshot",
        lambda **kwargs: snapshot or good_snapshot(),
    )
    monkeypatch.setattr(
        MODULE.ARCHIVE,
        "verify_phase2_portable_bundle_archive",
        lambda **kwargs: archive or good_archive(),
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
