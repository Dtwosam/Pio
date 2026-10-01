from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_bundle.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_bundle",
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
        "checkpoint": _validator(
            "validate_phase8_recursive_reentry_continuation_checkpoint_v24"
        ),
        "continuation_readiness": _validator(
            "validate_phase8_recursive_reentry_checkpoint_v24_continuation_supervision_readiness"
        ),
        "request": _validator(
            "validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_request"
        ),
        "signed_authorization_verification": _validator(
            "validate_verification"
        ),
        "execution_readiness": _validator(
            "validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_execution_readiness"
        ),
        "execution_receipt": _validator(
            "validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_execution_receipt"
        ),
        "post_audit": _validator(
            "validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_post_audit"
        ),
    }


def _base_lineage(production: Path, database: Path) -> dict:
    return {
        "production_repository": str(production),
        "pio_database_path": str(database),
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-3",
        "pair_id": "pair-4",
        "pool_address": "pool-4",
        "entry_observed_at": "2026-09-30T09:20:00+00:00",
        "incumbent_position_id": "p8-pair-4-incumbent",
        "challenger_position_id": "p8-pair-4-challenger",
        "requested_position_ids": [
            "p8-pair-4-incumbent",
            "p8-pair-4-challenger",
        ],
    }


def _historical() -> dict:
    return {
        "source_post_audit_sha256": "1" * 64,
        "source_execution_receipt_sha256": "2" * 64,
        "source_pair_entry_post_audit_sha256": "3" * 64,
        "pair_entry_request_sha256": "4" * 64,
        "pair_entry_input_verification_sha256": "5" * 64,
        "pair_lineage_sha256": "6" * 64,
        "latest_previous_tick_post_audit_sha256": "7" * 64,
        "source_final_evaluation_sha256": "8" * 64,
    }


def _artifacts(production: Path, database: Path) -> dict[str, dict]:
    lineage = _base_lineage(production, database)
    historical = _historical()
    checkpoint_sha = "a" * 64
    continuation_sha = "b" * 64
    request_sha = "c" * 64
    signed_sha = "d" * 64
    execution_readiness_sha = "e" * 64
    receipt_sha = "f" * 64
    post_audit_sha = "9" * 64
    pre_db = "0" * 64
    post_db = _sha(database.read_bytes())
    previous_tick = "2026-09-30T09:25:00+00:00"
    target_tick = "2026-09-30T09:30:00+00:00"
    evidence_cycle = "phase8-v24:pair-4:abc123"
    expected_run = "run-1"

    checkpoint = {
        **lineage,
        **historical,
        "checkpoint_sha256": checkpoint_sha,
        "checkpoint_state": "CONTINUE",
        "latest_tick_observed_at": previous_tick,
    }
    continuation = {
        **lineage,
        **historical,
        "source_checkpoint_sha256": checkpoint_sha,
        "readiness_sha256": continuation_sha,
        "readiness_status": "READY",
        "previous_tick_observed_at": previous_tick,
        "pio_database_sha256": pre_db,
    }
    request = {
        **lineage,
        **historical,
        "source_checkpoint_sha256": checkpoint_sha,
        "source_continuation_supervision_readiness_sha256": continuation_sha,
        "request_sha256": request_sha,
        "request_ready": True,
        "previous_tick_observed_at": previous_tick,
        "evidence_cycle_id": evidence_cycle,
        "target_chain_observed_at": target_tick,
        "pio_database_sha256": pre_db,
    }
    signed = {
        **lineage,
        **historical,
        "source_checkpoint_sha256": checkpoint_sha,
        "source_continuation_supervision_readiness_sha256": continuation_sha,
        "request_sha256": request_sha,
        "verification_sha256": signed_sha,
        "previous_tick_observed_at": previous_tick,
        "evidence_cycle_id": evidence_cycle,
        "target_chain_observed_at": target_tick,
        "pio_database_sha256": pre_db,
        "approval_payload_sha256": "1" * 64,
        "approval_signature_sha256": "2" * 64,
        "allowed_signers_sha256": "3" * 64,
        "approver_principal": "operator@example",
        "approval_id": "11111111-1111-4111-8111-111111111111",
        "expires_at": "2026-10-01T20:00:00Z",
    }
    execution_readiness = {
        **lineage,
        **historical,
        "source_checkpoint_sha256": checkpoint_sha,
        "saved_continuation_supervision_readiness_sha256": continuation_sha,
        "fresh_continuation_supervision_readiness_sha256": continuation_sha,
        "saved_request_sha256": request_sha,
        "fresh_request_sha256": request_sha,
        "saved_signed_authorization_verification_sha256": signed_sha,
        "fresh_signed_authorization_verification_sha256": signed_sha,
        "readiness_sha256": execution_readiness_sha,
        "previous_tick_observed_at": previous_tick,
        "evidence_cycle_id": evidence_cycle,
        "expected_run_id": expected_run,
        "target_chain_observed_at": target_tick,
        "pio_database_sha256": pre_db,
        "one_pair_evidence_tick_execution_readiness_ready": True,
    }
    receipt = {
        **lineage,
        **historical,
        "source_checkpoint_sha256": checkpoint_sha,
        "saved_request_sha256": request_sha,
        "signed_authorization_verification_sha256": signed_sha,
        "saved_readiness_sha256": execution_readiness_sha,
        "fresh_readiness_sha256": execution_readiness_sha,
        "receipt_sha256": receipt_sha,
        "previous_tick_observed_at": previous_tick,
        "evidence_cycle_id": evidence_cycle,
        "expected_run_id": expected_run,
        "target_chain_observed_at": target_tick,
        "approval_payload_sha256": signed["approval_payload_sha256"],
        "approval_signature_sha256": signed["approval_signature_sha256"],
        "allowed_signers_sha256": signed["allowed_signers_sha256"],
        "approver_principal": signed["approver_principal"],
        "approval_id": signed["approval_id"],
        "authorization_expires_at": signed["expires_at"],
        "pio_database_sha256_before": pre_db,
        "pio_database_sha256_after": post_db,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
        "paper_supervisor_tick_executed": True,
    }
    post_audit = {
        **lineage,
        **historical,
        "source_checkpoint_sha256": checkpoint_sha,
        "execution_receipt_sha256": receipt_sha,
        "post_audit_sha256": post_audit_sha,
        "previous_tick_observed_at": previous_tick,
        "evidence_cycle_id": evidence_cycle,
        "expected_run_id": expected_run,
        "target_chain_observed_at": target_tick,
        "receipt_database_sha256_after": post_db,
        "receipt_wal_sha256_after": None,
        "receipt_shm_sha256_after": None,
        "audit_database_sha256_before": post_db,
        "audit_database_sha256_after": post_db,
        "audit_wal_sha256_before": None,
        "audit_wal_sha256_after": None,
        "audit_shm_sha256_before": None,
        "audit_shm_sha256_after": None,
        "post_tick_audit_ready": True,
        "receipt_tick_status": "COMPLETE",
        "receipt_pair_tick_complete": True,
        "receipt_pair_tick_partial_failure": False,
        "pair_both_open": True,
        "pair_any_closed": False,
        "next_debt_type": "PAPER_CHALLENGER_EVIDENCE_REQUIRED",
        "next_scope": "challenger-1",
        "continuation_route": (
            "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V24_NEXT_EVIDENCE_TICK_REVIEW"
        ),
        "next_evidence_tick_review_ready": True,
        "tick_recovery_review_ready": False,
        "terminal_pair_evaluation_ready": False,
    }
    return {
        "checkpoint": checkpoint,
        "continuation_readiness": continuation,
        "request": request,
        "signed_authorization_verification": signed,
        "execution_readiness": execution_readiness,
        "execution_receipt": receipt,
        "post_audit": post_audit,
    }


def _write_artifacts(root: Path, artifacts: dict[str, dict]) -> dict[str, Path]:
    paths = {}
    for name, value in artifacts.items():
        path = root / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path
    return paths


def _build(monkeypatch):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"post-audit-database")
    artifacts = _artifacts(production, database)
    paths = _write_artifacts(root, artifacts)
    monkeypatch.setattr(MODULE, "_load_reviewed", lambda source: _fake_modules())

    def run():
        return MODULE.build_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_bundle(
            repository=production,
            source_tree=ROOT,
            checkpoint_path=paths["checkpoint"],
            continuation_readiness_path=paths["continuation_readiness"],
            request_path=paths["request"],
            signed_authorization_verification_path=paths[
                "signed_authorization_verification"
            ],
            execution_readiness_path=paths["execution_readiness"],
            execution_receipt_path=paths["execution_receipt"],
            post_audit_path=paths["post_audit"],
        )

    return temp, database, artifacts, paths, run


def _reseal(bundle: dict) -> None:
    identity = {field: bundle[field] for field in MODULE.BUNDLE_FIELDS}
    bundle["bundle_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_builds_sealed_read_only_bundle(monkeypatch):
    temp, _, _, _, run = _build(monkeypatch)
    try:
        bundle = run()
    finally:
        temp.cleanup()

    assert bundle["lineage_verified"] is True
    assert bundle["all_native_validators_passed"] is True
    assert bundle["database_matches_final_audit"] is True
    assert bundle["bundle_read_only"] is True
    assert bundle["requires_separate_next_action_authorization"] is True
    assert bundle["receipt_pair_tick_complete"] is True
    assert bundle["next_evidence_tick_review_ready"] is True
    assert bundle["paper_supervisor_tick_authorized"] is False
    assert bundle["paper_trading_authorized"] is False
    assert bundle["live_submit_authorized"] is False
    assert bundle["phase8_promotion_authorized"] is False
    assert set(bundle["artifact_file_sha256"]) == set(MODULE.ARTIFACT_NAMES)
    MODULE.validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_bundle(
        bundle
    )


def test_mixed_request_lineage_fails_closed(monkeypatch):
    temp, _, artifacts, paths, run = _build(monkeypatch)
    try:
        artifacts["request"]["source_checkpoint_sha256"] = "9" * 64
        paths["request"].write_text(
            json.dumps(artifacts["request"]),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="checkpoint digest mismatch"):
            run()
    finally:
        temp.cleanup()


def test_database_change_after_post_audit_fails_closed(monkeypatch):
    temp, database, _, _, run = _build(monkeypatch)
    try:
        database.write_bytes(b"changed-after-audit")
        with pytest.raises(
            ValueError,
            match="database changed after checkpoint v24 post-audit",
        ):
            run()
    finally:
        temp.cleanup()


def test_symlinked_artifact_is_rejected(monkeypatch):
    temp, _, _, paths, run = _build(monkeypatch)
    try:
        target = paths["request"]
        alias = target.parent / "request-alias.json"
        alias.symlink_to(target)
        paths["request"].unlink()
        paths["request"].symlink_to(alias)
        with pytest.raises(ValueError, match="must not be a symlink"):
            run()
    finally:
        temp.cleanup()


def test_resealed_bundle_cannot_authorize_live_submit(monkeypatch):
    temp, _, _, _, run = _build(monkeypatch)
    try:
        bundle = run()
    finally:
        temp.cleanup()

    bundle["live_submit_authorized"] = True
    _reseal(bundle)
    with pytest.raises(
        ValueError,
        match="live_submit_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_bundle(
            bundle
        )


def test_resealed_bundle_cannot_authorize_another_paper_tick(monkeypatch):
    temp, _, _, _, run = _build(monkeypatch)
    try:
        bundle = run()
    finally:
        temp.cleanup()

    bundle["paper_supervisor_tick_authorized"] = True
    _reseal(bundle)
    with pytest.raises(
        ValueError,
        match="paper_supervisor_tick_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_bundle(
            bundle
        )


def test_bundle_has_no_execution_or_database_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "sqlite3.connect" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"phase8_promotion_authorized": False' in source
