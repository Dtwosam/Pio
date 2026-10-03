from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_mutation_post_audit_catalog_freshness.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load(TOOL, "check_phase2_mutation_post_audit_catalog_freshness")


def entry(
    artifact_sha,
    receipt_sha,
    *,
    path="/archive/a.post-audit.json",
    source="a" * 40,
    prior="SMOKE_REQUIRED",
    current="TIMER_ACTIVATION_READY",
    action="ACTIVATE_EVIDENCE_TIMER",
    tool="activate_phase2_isolated_timer.py",
    flag="--apply",
):
    return {
        "artifact_path": path,
        "artifact_sha256": artifact_sha,
        "verification_status": "VERIFIED",
        "failure_category": None,
        "audit_source_commit": source,
        "execution_receipt_sha256": receipt_sha,
        "prior_state": prior,
        "current_state": current,
        "current_next_action": action,
        "current_next_tool": tool,
        "current_next_mutation_flag": flag,
    }


class LiveEntry:
    def __init__(self, record):
        self.record = record

    def to_record(self):
        return dict(self.record)


def snapshot_report(*, verified=True, seen=2):
    return SimpleNamespace(
        snapshot_path="/archive/catalog.json",
        snapshot_sha256="f" * 64,
        snapshot_verified=verified,
        artifacts_seen=seen,
        historical_snapshot_only=True,
        authorizes_next_action=False,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def live_report(entries, *, all_verified=True, failed=0):
    return SimpleNamespace(
        artifact_directory="/current/archive",
        pattern="*.post-audit.json",
        artifacts_seen=len(entries),
        artifacts_verified=len(entries) - failed,
        artifacts_failed=failed,
        duplicate_artifact_hashes=0,
        duplicate_receipt_hashes=0,
        entries=tuple(LiveEntry(value) for value in entries),
        catalog_stable_during_scan=True,
        all_verified=all_verified,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


SNAPSHOT_ENTRIES = [
    entry("1" * 64, "2" * 64, path="/old/a.post-audit.json"),
    entry(
        "3" * 64,
        "4" * 64,
        path="/old/b.post-audit.json",
        source="b" * 40,
        prior="TIMER_ACTIVATION_READY",
        current="RUNNING_HEALTHY",
        action="MONITOR_ZERO_RPC_STATUS",
        tool="check_phase2_isolated_operator_status.py",
        flag=None,
    ),
]


def install(monkeypatch, *, snapshot=None, live=None, snapshot_entries=None):
    snapshot = snapshot or snapshot_report()
    live = live or live_report(SNAPSHOT_ENTRIES)
    snapshot_entries = snapshot_entries or SNAPSHOT_ENTRIES

    monkeypatch.setattr(
        MODULE.SNAPSHOT_VERIFY,
        "verify_phase2_post_audit_catalog_snapshot",
        lambda **kwargs: snapshot,
    )
    monkeypatch.setattr(
        MODULE,
        "_load_snapshot_catalog",
        lambda path: {"entries": list(snapshot_entries)},
    )
    monkeypatch.setattr(
        MODULE.CATALOG,
        "build_phase2_post_audit_catalog",
        lambda **kwargs: live,
    )


def test_fresh_reverification_matches_identity_set_across_path_move(monkeypatch):
    moved = [
        {**SNAPSHOT_ENTRIES[0], "artifact_path": "/new/a.post-audit.json"},
        {**SNAPSHOT_ENTRIES[1], "artifact_path": "/new/b.post-audit.json"},
    ]
    install(monkeypatch, live=live_report(moved))

    report = MODULE.freshly_reverify_phase2_post_audit_catalog(
        snapshot_path="/archive/catalog.json",
        artifact_directory="/new",
    )

    assert report.snapshot_verified is True
    assert report.artifacts_reverified is True
    assert report.artifact_paths_ignored_for_identity is True
    assert report.evidence_identity_sets_match is True
    assert report.missing_evidence_identities == 0
    assert report.unexpected_evidence_identities == 0
    assert report.fresh_reverification_verified is True
    assert report.authorizes_next_action is False
    assert report.read_only is True
    assert report.rpc_called is False


def test_fresh_reverification_detects_missing_and_unexpected_identity(monkeypatch):
    replacement = entry(
        "9" * 64,
        "8" * 64,
        path="/new/replacement.post-audit.json",
    )
    live = [SNAPSHOT_ENTRIES[0], replacement]
    install(monkeypatch, live=live_report(live))

    report = MODULE.freshly_reverify_phase2_post_audit_catalog(
        snapshot_path="/archive/catalog.json",
        artifact_directory="/new",
    )

    assert report.evidence_identity_sets_match is False
    assert report.missing_evidence_identities == 1
    assert report.unexpected_evidence_identities == 1
    assert report.fresh_reverification_verified is False


def test_unverified_snapshot_cannot_be_freshly_verified(monkeypatch):
    install(monkeypatch, snapshot=snapshot_report(verified=False))

    report = MODULE.freshly_reverify_phase2_post_audit_catalog(
        snapshot_path="/archive/catalog.json",
        artifact_directory="/archive",
    )

    assert report.snapshot_verified is False
    assert report.evidence_identity_sets_match is True
    assert report.fresh_reverification_verified is False


def test_live_catalog_must_be_fully_verified(monkeypatch):
    install(
        monkeypatch,
        live=live_report(SNAPSHOT_ENTRIES, all_verified=False, failed=1),
    )

    report = MODULE.freshly_reverify_phase2_post_audit_catalog(
        snapshot_path="/archive/catalog.json",
        artifact_directory="/archive",
    )

    assert report.live_artifacts_failed == 1
    assert report.live_catalog_all_verified is False
    assert report.fresh_reverification_verified is False


def test_fresh_reverification_rejects_snapshot_boundary_crossing(monkeypatch):
    bad = snapshot_report()
    bad.authorizes_next_action = True
    install(monkeypatch, snapshot=bad)

    with pytest.raises(ValueError, match="historical evidence boundary"):
        MODULE.freshly_reverify_phase2_post_audit_catalog(
            snapshot_path="/archive/catalog.json",
            artifact_directory="/archive",
        )


def test_fresh_reverification_rejects_live_catalog_boundary_crossing(
    monkeypatch,
):
    bad = live_report(SNAPSHOT_ENTRIES)
    bad.rpc_called = True
    install(monkeypatch, live=bad)

    with pytest.raises(ValueError, match="read-only boundary"):
        MODULE.freshly_reverify_phase2_post_audit_catalog(
            snapshot_path="/archive/catalog.json",
            artifact_directory="/archive",
        )


def test_metadata_drift_is_evidence_identity_drift(monkeypatch):
    changed = [
        SNAPSHOT_ENTRIES[0],
        {
            **SNAPSHOT_ENTRIES[1],
            "current_next_action": "DIFFERENT_ACTION",
        },
    ]
    install(monkeypatch, live=live_report(changed))

    report = MODULE.freshly_reverify_phase2_post_audit_catalog(
        snapshot_path="/archive/catalog.json",
        artifact_directory="/archive",
    )

    assert report.evidence_identity_sets_match is False
    assert report.fresh_reverification_verified is False



def test_fresh_reverification_rejects_sensitive_archive_path(monkeypatch):
    install(monkeypatch)

    with pytest.raises(ValueError, match="sensitive text"):
        MODULE.freshly_reverify_phase2_post_audit_catalog(
            snapshot_path="/archive/catalog.json",
            artifact_directory="/tmp/api-key=secret",
        )
