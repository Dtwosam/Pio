from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy/tools/check_phase2_mutation_post_audit_catalog_handoff_snapshot.py"
)
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_mutation_post_audit_catalog_handoff_snapshot",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def good_handoff(**overrides):
    values = {
        "snapshot_path": "/archive/catalog.json",
        "snapshot_sha256": "1" * 64,
        "snapshot_verified": True,
        "historical_artifacts_seen": 2,
        "historical_artifacts_verified": 2,
        "historical_catalog_source_commit": "a" * 40,
        "historical_snapshot_only": True,
        "historical_authorizes_next_action": False,
        "fresh_reverification_requested": True,
        "artifacts_reverified": True,
        "fresh_reverification_verified": True,
        "fresh_snapshot_identity_matches": True,
        "fresh_artifact_directory": "/archive/post-audits",
        "current_state": "TIMER_ACTIVATION_READY",
        "current_next_action": "ACTIVATE_EVIDENCE_TIMER",
        "current_next_tool": "activate_phase2_isolated_timer.py",
        "current_next_parameters": {
            "runtime_root": "/opt/pio-phase2-runtime",
            "max_receipt_age_seconds": 1800,
        },
        "current_next_mutation_flag": "--apply",
        "current_attention_required": False,
        "current_blockers": [],
        "provider_rate_limit_incident": False,
        "provider_rate_limit_paused": False,
        "evidence_lineage_verified": True,
        "snapshot_influenced_current_action": False,
        "next_action_source": "CURRENT_LIFECYCLE_HANDOFF",
        "attention_required": False,
        "blockers": [],
        "authorizes_next_action": False,
        "requires_fresh_separate_mutation_authorization": True,
        "read_only": True,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
        "mutation_executed": False,
    }
    values.update(overrides)
    return values


def write_snapshot(tmp_path: Path, handoff=None, **payload_overrides):
    handoff = good_handoff() if handoff is None else handoff
    payload = {
        "format_version": 1,
        "artifact_type": (
            "PHASE2_MUTATION_POST_AUDIT_CATALOG_HANDOFF_SNAPSHOT_V1"
        ),
        "handoff_payload_sha256": MODULE._canonical_sha256(handoff),
        "handoff_source_commit": "b" * 40,
        "handoff_tool_sha256": "c" * 64,
        "handoff": handoff,
    }
    payload.update(payload_overrides)
    path = tmp_path / "handoff.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def install_valid_lineage(monkeypatch):
    monkeypatch.setattr(MODULE, "_commit_present", lambda root, commit: True)
    monkeypatch.setattr(
        MODULE,
        "_is_ancestor_of_head",
        lambda root, commit: True,
    )
    monkeypatch.setattr(
        MODULE,
        "_historical_file_sha256",
        lambda root, *, commit, relative_path: "c" * 64,
    )


def test_verify_saved_handoff_snapshot_accepts_valid_non_authorizing_evidence(
    tmp_path,
    monkeypatch,
):
    path = write_snapshot(tmp_path)
    install_valid_lineage(monkeypatch)

    report = MODULE.verify_phase2_post_audit_catalog_handoff_snapshot(
        snapshot_path=path,
        repository_root=ROOT,
    )

    assert report.handoff_snapshot_verified is True
    assert report.handoff_payload_sha256_matches is True
    assert report.handoff_source_commit_present is True
    assert report.handoff_source_is_ancestor_of_current_head is True
    assert report.handoff_tool_sha256_matches is True
    assert report.evidence_lineage_verified is True
    assert report.non_authorizing_boundary_valid is True
    assert report.fresh_reverification_requested is True
    assert report.fresh_reverification_recorded_valid is True
    assert report.current_state == "TIMER_ACTIVATION_READY"
    assert report.current_next_action == "ACTIVATE_EVIDENCE_TIMER"
    assert report.historical_handoff_only is True
    assert report.current_lifecycle_rechecked is False
    assert report.authorizes_next_action is False
    assert report.requires_fresh_separate_mutation_authorization is True
    assert report.read_only is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False
    assert report.mutation_executed is False


def test_verify_saved_handoff_snapshot_accepts_static_catalog_mode(
    tmp_path,
    monkeypatch,
):
    handoff = good_handoff(
        fresh_reverification_requested=False,
        artifacts_reverified=False,
        fresh_reverification_verified=None,
        fresh_snapshot_identity_matches=None,
        fresh_artifact_directory=None,
    )
    path = write_snapshot(tmp_path, handoff=handoff)
    install_valid_lineage(monkeypatch)

    report = MODULE.verify_phase2_post_audit_catalog_handoff_snapshot(
        snapshot_path=path,
        repository_root=ROOT,
    )

    assert report.handoff_snapshot_verified is True
    assert report.fresh_reverification_requested is False
    assert report.fresh_reverification_recorded_valid is True


def test_verify_saved_handoff_snapshot_detects_payload_tamper(
    tmp_path,
    monkeypatch,
):
    handoff = good_handoff()
    path = write_snapshot(tmp_path, handoff=handoff)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["handoff"]["current_state"] = "RUNNING_HEALTHY"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    install_valid_lineage(monkeypatch)

    report = MODULE.verify_phase2_post_audit_catalog_handoff_snapshot(
        snapshot_path=path,
        repository_root=ROOT,
    )

    assert report.handoff_payload_sha256_matches is False
    assert report.handoff_snapshot_verified is False


@pytest.mark.parametrize(
    "field,value,match",
    [
        (
            "authorizes_next_action",
            True,
            "non-authorizing boundary",
        ),
        (
            "requires_fresh_separate_mutation_authorization",
            False,
            "non-authorizing boundary",
        ),
        (
            "snapshot_influenced_current_action",
            True,
            "non-authorizing boundary",
        ),
        (
            "evidence_lineage_verified",
            False,
            "evidence lineage",
        ),
        (
            "historical_authorizes_next_action",
            True,
            "historical evidence became authorizing",
        ),
    ],
)
def test_verify_saved_handoff_snapshot_rejects_authority_escalation(
    tmp_path,
    monkeypatch,
    field,
    value,
    match,
):
    handoff = good_handoff(**{field: value})
    path = write_snapshot(tmp_path, handoff=handoff)
    install_valid_lineage(monkeypatch)

    with pytest.raises(ValueError, match=match):
        MODULE.verify_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=path,
            repository_root=ROOT,
        )


def test_verify_saved_handoff_snapshot_rejects_inconsistent_fresh_record(
    tmp_path,
    monkeypatch,
):
    handoff = good_handoff(fresh_snapshot_identity_matches=False)
    path = write_snapshot(tmp_path, handoff=handoff)
    install_valid_lineage(monkeypatch)

    with pytest.raises(ValueError, match="fresh verification record"):
        MODULE.verify_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=path,
            repository_root=ROOT,
        )


def test_verify_saved_handoff_snapshot_rejects_sensitive_content(
    tmp_path,
    monkeypatch,
):
    handoff = good_handoff(
        current_next_parameters={
            "note": "https://rpc.invalid/?api-key=secret",
        }
    )
    path = write_snapshot(tmp_path, handoff=handoff)
    install_valid_lineage(monkeypatch)

    with pytest.raises(ValueError, match="sensitive text"):
        MODULE.verify_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=path,
            repository_root=ROOT,
        )


def test_verify_saved_handoff_snapshot_requires_private_file(
    tmp_path,
):
    path = write_snapshot(tmp_path)
    path.chmod(0o644)

    with pytest.raises(ValueError, match="permissions must be 0600"):
        MODULE.verify_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=path,
            repository_root=ROOT,
        )


def test_verify_saved_handoff_snapshot_rejects_unknown_schema(
    tmp_path,
    monkeypatch,
):
    handoff = good_handoff()
    handoff["unexpected"] = True
    path = write_snapshot(tmp_path, handoff=handoff)
    install_valid_lineage(monkeypatch)

    with pytest.raises(ValueError, match="handoff schema is invalid"):
        MODULE.verify_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=path,
            repository_root=ROOT,
        )


def test_historical_handoff_tool_hash_matches_current_head():
    head = MODULE._run_git(ROOT, "rev-parse", "HEAD")
    assert head.returncode == 0
    commit = head.stdout.decode("utf-8").strip()
    historical = MODULE._historical_file_sha256(
        ROOT,
        commit=commit,
        relative_path=MODULE.HANDOFF_TOOL_RELATIVE,
    )
    current = (
        ROOT / MODULE.HANDOFF_TOOL_RELATIVE
    ).read_bytes()

    assert historical == MODULE.hashlib.sha256(current).hexdigest()
