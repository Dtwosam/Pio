from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase8_recursive_reentry_checkpoint_v25_operator_session_archive.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_phase8_recursive_reentry_checkpoint_v25_operator_session_archive",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validator(name: str):
    def validate(value):
        assert isinstance(value, dict)

    return SimpleNamespace(**{name: validate})


def _fake_modules():
    return {
        "surface": _validator(
            "validate_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity"
        ),
        "preflight": _validator(
            "validate_phase8_recursive_reentry_checkpoint_v25_operator_preflight"
        ),
        "status": _validator(
            "validate_phase8_recursive_reentry_checkpoint_v25_artifact_status"
        ),
        "handoff": _validator(
            "validate_phase8_recursive_reentry_checkpoint_v25_evidence_handoff"
        ),
        "bundle": _validator(
            "validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle"
        ),
    }


def _write(path: Path, value: dict) -> str:
    payload = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(payload)
    return _sha(payload)


def _reports(root: Path):
    core_blob = "a" * 40
    manifest_sha = "b" * 64
    tool_blobs = {
        f"deploy/tools/core-{index}.py": f"{index:x}" * 40
        for index in range(8)
    }
    artifact_hashes = {
        "checkpoint": "1" * 64,
        "continuation_readiness": "2" * 64,
        "request": "3" * 64,
        "signed_authorization_verification": "4" * 64,
        "execution_readiness": "5" * 64,
        "execution_receipt": "6" * 64,
        "post_audit": "7" * 64,
    }
    production = "/opt/pio"
    database = "/opt/pio/data/pio.db"
    pair_id = "pair-25"
    previous_pair_id = "pair-24"
    expected_run = "run-25"
    route = "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_NEXT_EVIDENCE_TICK_REVIEW"

    surface = {
        "source_tree": str(ROOT),
        "core_execution_manifest_git_blob": core_blob,
        "support_surface_verified": True,
        "core_execution_manifest_verified": True,
        "real_v25_evidence_required_for_future_checkpoint": True,
        "next_action_authorized": False,
        "future_checkpoint_refresh_authorized": False,
        "paper_supervisor_tick_authorized": False,
        "live_submit_authorized": False,
        "phase8_promotion_authorized": False,
        "surface_integrity_sha256": "8" * 64,
    }
    preflight = {
        "source_tree": str(ROOT),
        "production_repository": production,
        "pio_database_path": database,
        "reviewed_manifest_blob": core_blob,
        "manifest_sha256": manifest_sha,
        "reviewed_tool_blobs": tool_blobs,
        "preflight_ready": True,
        "manifest_valid": True,
        "reviewed_source_tools_verified": True,
        "paper_supervisor_tick_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "phase8_promotion_authorized": False,
        "preflight_sha256": "9" * 64,
    }
    bundle = {
        "production_repository": production,
        "pio_database_path": database,
        "artifact_file_sha256": artifact_hashes,
        "bundle_sha256": "a" * 64,
        "post_audit_sha256": "b" * 64,
        "execution_receipt_sha256": "c" * 64,
        "pair_id": pair_id,
        "previous_pair_id": previous_pair_id,
        "expected_run_id": expected_run,
        "continuation_route": route,
        "current_database_sha256": "d" * 64,
        "current_wal_sha256": None,
        "current_shm_sha256": None,
    }
    bundle_path = root / "evidence-bundle-v25.json"
    bundle_file_sha = _write(bundle_path, bundle)

    role_hashes = {
        "bundled-canonical-checkpoint": artifact_hashes["checkpoint"],
        "continuation-readiness": artifact_hashes["continuation_readiness"],
        "tick-request": artifact_hashes["request"],
        "detached-authorization": artifact_hashes[
            "signed_authorization_verification"
        ],
        "execution-readiness": artifact_hashes["execution_readiness"],
        "one-shot-paper-executor": artifact_hashes["execution_receipt"],
        "post-execution-audit": artifact_hashes["post_audit"],
        "evidence-bundle": bundle_file_sha,
    }
    stages = [
        {
            "role": role,
            "artifact_sha256": digest,
            "validation_status": "VALID",
        }
        for role, digest in role_hashes.items()
    ]
    status = {
        "source_tree": str(ROOT),
        "reviewed_manifest_blob": core_blob,
        "manifest_sha256": manifest_sha,
        "reviewed_tool_blobs": tool_blobs,
        "artifact_chain_complete": True,
        "valid_prefix_length": 8,
        "next_boundary": "EVIDENCE_CHAIN_COMPLETE",
        "invalid_artifact_present": False,
        "out_of_order_artifacts_present": False,
        "stages": stages,
        "next_action_authorized": False,
        "paper_supervisor_tick_authorized": False,
        "live_submit_authorized": False,
        "phase8_promotion_authorized": False,
        "artifact_status_sha256": "e" * 64,
    }
    handoff = {
        "production_repository": production,
        "pio_database_path": database,
        "source_evidence_bundle_sha256": bundle["bundle_sha256"],
        "source_post_audit_sha256": bundle["post_audit_sha256"],
        "source_execution_receipt_sha256": bundle[
            "execution_receipt_sha256"
        ],
        "pair_id": pair_id,
        "previous_pair_id": previous_pair_id,
        "expected_run_id": expected_run,
        "continuation_route": route,
        "bundle_database_sha256": bundle["current_database_sha256"],
        "bundle_wal_sha256": None,
        "bundle_shm_sha256": None,
        "handoff_state": "CONTINUE",
        "evidence_handoff_ready": True,
        "future_checkpoint_refresh_authorized": False,
        "paper_supervisor_tick_authorized": False,
        "live_submit_authorized": False,
        "phase8_promotion_authorized": False,
        "handoff_sha256": "f" * 64,
    }

    paths = {
        "surface": root / "operator-surface-integrity-v25.json",
        "preflight": root / "operator-preflight-v25.json",
        "status": root / "artifact-status-v25.json",
        "bundle": bundle_path,
        "handoff": root / "evidence-handoff-v25.json",
    }
    _write(paths["surface"], surface)
    _write(paths["preflight"], preflight)
    _write(paths["status"], status)
    _write(paths["handoff"], handoff)
    return paths, surface, preflight, status, bundle, handoff


def _build(monkeypatch):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    paths, surface, preflight, status, bundle, handoff = _reports(root)
    monkeypatch.setattr(MODULE, "_load_reviewed", lambda source: _fake_modules())

    def run():
        return MODULE.build_phase8_recursive_reentry_checkpoint_v25_operator_session_archive(
            source_tree=ROOT,
            surface_integrity_path=paths["surface"],
            preflight_path=paths["preflight"],
            artifact_status_path=paths["status"],
            evidence_bundle_path=paths["bundle"],
            evidence_handoff_path=paths["handoff"],
        )

    return temp, paths, surface, preflight, status, bundle, handoff, run


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["session_archive_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_session_archive_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_reviewed_native_validators_are_loadable():
    modules = MODULE._load_reviewed(ROOT)
    assert callable(
        modules["surface"].validate_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity
    )
    assert callable(
        modules["preflight"].validate_phase8_recursive_reentry_checkpoint_v25_operator_preflight
    )
    assert callable(
        modules["status"].validate_phase8_recursive_reentry_checkpoint_v25_artifact_status
    )
    assert callable(
        modules["handoff"].validate_phase8_recursive_reentry_checkpoint_v25_evidence_handoff
    )
    assert callable(
        modules["bundle"].validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle
    )


def test_builds_historical_non_authorizing_session_archive(monkeypatch):
    temp, _, _, _, _, bundle, handoff, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["evidence_bundle_sha256"] == bundle["bundle_sha256"]
    assert report["evidence_handoff_sha256"] == handoff["handoff_sha256"]
    assert report["core_artifact_hashes_match_bundle"] is True
    assert report["core_artifact_chain_complete"] is True
    assert report["session_lineage_verified"] is True
    assert report["archive_read_only"] is True
    assert report["historical_archive_only"] is True
    assert report["fresh_preflight_performed"] is False
    assert report["detached_signature_reverification_performed"] is False
    assert report["current_database_revalidation_performed"] is False
    assert report["next_action_authorized"] is False
    assert report["future_checkpoint_refresh_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["phase8_promotion_authorized"] is False
    MODULE.validate_phase8_recursive_reentry_checkpoint_v25_operator_session_archive(
        report
    )


def test_core_artifact_hash_drift_fails_closed(monkeypatch):
    temp, paths, _, _, status, _, _, run = _build(monkeypatch)
    try:
        status["stages"][0]["artifact_sha256"] = "0" * 64
        _write(paths["status"], status)
        with pytest.raises(ValueError, match="core artifact hash mismatch"):
            run()
    finally:
        temp.cleanup()


def test_bundle_handoff_digest_drift_fails_closed(monkeypatch):
    temp, paths, _, _, _, _, handoff, run = _build(monkeypatch)
    try:
        handoff["source_evidence_bundle_sha256"] = "0" * 64
        _write(paths["handoff"], handoff)
        with pytest.raises(ValueError, match="bundle/handoff digest mismatch"):
            run()
    finally:
        temp.cleanup()


def test_incomplete_artifact_chain_fails_closed(monkeypatch):
    temp, paths, _, _, status, _, _, run = _build(monkeypatch)
    try:
        status["artifact_chain_complete"] = False
        status["valid_prefix_length"] = 7
        status["next_boundary"] = "READ_ONLY_ARTIFACT_REQUIRED"
        _write(paths["status"], status)
        with pytest.raises(ValueError, match="artifact chain is incomplete"):
            run()
    finally:
        temp.cleanup()


def test_core_tool_set_drift_fails_closed(monkeypatch):
    temp, paths, _, _, status, _, _, run = _build(monkeypatch)
    try:
        status["reviewed_tool_blobs"] = dict(status["reviewed_tool_blobs"])
        first = next(iter(status["reviewed_tool_blobs"]))
        status["reviewed_tool_blobs"][first] = "f" * 40
        _write(paths["status"], status)
        with pytest.raises(ValueError, match="reviewed tool set mismatch"):
            run()
    finally:
        temp.cleanup()


def test_symlinked_support_report_is_rejected(monkeypatch):
    temp, paths, _, _, _, _, _, _ = _build(monkeypatch)
    try:
        actual = paths["preflight"].with_name("preflight-actual.json")
        actual.write_bytes(paths["preflight"].read_bytes())
        paths["preflight"].unlink()
        paths["preflight"].symlink_to(actual)
        with pytest.raises(ValueError, match="must not be a symlink"):
            MODULE.build_phase8_recursive_reentry_checkpoint_v25_operator_session_archive(
                source_tree=ROOT,
                surface_integrity_path=paths["surface"],
                preflight_path=paths["preflight"],
                artifact_status_path=paths["status"],
                evidence_bundle_path=paths["bundle"],
                evidence_handoff_path=paths["handoff"],
            )
    finally:
        temp.cleanup()


def test_resealed_archive_cannot_authorize_future_checkpoint(monkeypatch):
    temp, _, _, _, _, _, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["future_checkpoint_refresh_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="future_checkpoint_refresh_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_operator_session_archive(
            report
        )


def test_resealed_archive_cannot_claim_fresh_database_check(monkeypatch):
    temp, _, _, _, _, _, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["current_database_revalidation_performed"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="current_database_revalidation_performed=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_operator_session_archive(
            report
        )


def test_session_archive_has_no_execution_or_database_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "sqlite3.connect" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert "subprocess.run" not in source
    assert "os.system" not in source
    assert '"fresh_preflight_performed": False' in source
    assert '"current_database_revalidation_performed": False' in source
    assert '"future_checkpoint_refresh_authorized": False' in source
    assert '"next_action_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"phase8_promotion_authorized": False' in source
