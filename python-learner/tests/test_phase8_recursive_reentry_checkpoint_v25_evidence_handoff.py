from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase8_recursive_reentry_checkpoint_v25_evidence_handoff.py"
)
SPEC = importlib.util.spec_from_file_location(
    "check_phase8_recursive_reentry_checkpoint_v25_evidence_handoff",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _BundleModule:
    @staticmethod
    def validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle(
        value,
    ):
        assert isinstance(value, dict)


class _AuditModule:
    @staticmethod
    def validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_post_audit(
        value,
    ):
        assert isinstance(value, dict)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _artifacts(production: Path, database: Path, state: str = "CONTINUE"):
    digest = _sha(database.read_bytes())
    route_flags = {
        "next_evidence_tick_review_ready": state == "CONTINUE",
        "terminal_pair_evaluation_ready": state == "TERMINAL",
        "tick_recovery_review_ready": state == "RECOVERY",
    }
    route = {
        "CONTINUE": "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_NEXT_EVIDENCE_TICK_REVIEW",
        "TERMINAL": "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_TERMINAL_EVALUATION_REVIEW",
        "RECOVERY": "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_TICK_RECOVERY_REVIEW",
    }[state]
    next_debt = {
        "CONTINUE": "PAPER_CHALLENGER_EVIDENCE_REQUIRED",
        "TERMINAL": "PAPER_PAIR_TERMINAL_EVALUATION_REQUIRED",
        "RECOVERY": "PAPER_PAIR_TICK_RECOVERY_REQUIRED",
    }[state]

    audit = {
        "post_audit_sha256": "a" * 64,
        "execution_receipt_sha256": "b" * 64,
        "post_tick_audit_ready": True,
        "separate_next_action_authorization_required": True,
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
        "evidence_cycle_id": "phase8-v25:pair-4:abc123",
        "expected_run_id": "expected-run",
        "target_chain_observed_at": "2026-09-30T09:30:00+00:00",
        "receipt_tick_status": "COMPLETE",
        "receipt_pair_tick_complete": True,
        "receipt_pair_tick_partial_failure": False,
        "pair_both_open": state == "CONTINUE",
        "pair_any_closed": state == "TERMINAL",
        "next_debt_type": next_debt,
        "next_scope": "pair-4",
        "continuation_route": route,
        **route_flags,
        "audit_database_sha256_after": digest,
        "audit_wal_sha256_after": None,
        "audit_shm_sha256_after": None,
        "source_pair_entry_post_audit_sha256": "c" * 64,
        "pair_entry_request_sha256": "d" * 64,
        "pair_entry_input_verification_sha256": "e" * 64,
        "source_final_evaluation_sha256": "f" * 64,
    }
    audit["pair_lineage_sha256"] = MODULE._sha256_bytes(
        MODULE._canonical_bytes(MODULE._pair_lineage(audit))
    )

    bundle = {
        "bundle_sha256": "1" * 64,
        "post_audit_sha256": audit["post_audit_sha256"],
        "database_matches_final_audit": True,
        "lineage_verified": True,
        "all_native_validators_passed": True,
        "bundle_read_only": True,
        "requires_separate_next_action_authorization": True,
        "paper_supervisor_tick_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
        "current_database_sha256": digest,
        "current_wal_sha256": None,
        "current_shm_sha256": None,
    }
    for field in (
        "production_repository",
        "pio_database_path",
        "active_cycle_id",
        "incumbent_model_id",
        "challenger_model_id",
        "account_id",
        "previous_pair_id",
        "pair_id",
        "pool_address",
        "entry_observed_at",
        "incumbent_position_id",
        "challenger_position_id",
        "requested_position_ids",
        "evidence_cycle_id",
        "expected_run_id",
        "target_chain_observed_at",
        "execution_receipt_sha256",
        "receipt_tick_status",
        "receipt_pair_tick_complete",
        "receipt_pair_tick_partial_failure",
        "pair_both_open",
        "pair_any_closed",
        "next_debt_type",
        "next_scope",
        "continuation_route",
        "next_evidence_tick_review_ready",
        "tick_recovery_review_ready",
        "terminal_pair_evaluation_ready",
    ):
        bundle[field] = audit[field]
    return bundle, audit


def _build(monkeypatch, state="CONTINUE"):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"stable-production-db")
    bundle, audit = _artifacts(production.resolve(), database.resolve(), state)
    bundle_path = _write(root / "bundle.json", bundle)
    audit_path = _write(root / "post-audit.json", audit)
    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (_BundleModule, _AuditModule),
    )

    def run():
        return MODULE.build_phase8_recursive_reentry_checkpoint_v25_evidence_handoff(
            repository=production,
            source_tree=ROOT,
            evidence_bundle_path=bundle_path,
            post_audit_path=audit_path,
        )

    return temp, production, database, bundle, audit, bundle_path, audit_path, run


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["handoff_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_v25_handoff_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_reviewed_v25_handoff_validators_are_loadable():
    bundle_module, audit_module = MODULE._load_reviewed(ROOT)
    assert callable(
        bundle_module.validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle
    )
    assert callable(
        audit_module.validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_post_audit
    )


@pytest.mark.parametrize(
    ("state", "field"),
    [
        ("CONTINUE", "continuation_handoff_ready"),
        ("TERMINAL", "terminal_handoff_ready"),
        ("RECOVERY", "recovery_handoff_ready"),
    ],
)
def test_builds_read_only_route_specific_handoff(monkeypatch, state, field):
    temp, _, _, _, _, _, _, run = _build(monkeypatch, state)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["handoff_state"] == state
    assert report[field] is True
    assert report["evidence_handoff_ready"] is True
    assert report["bundle_post_audit_binding_verified"] is True
    assert report["bundle_route_binding_verified"] is True
    assert report["pair_lineage_verified"] is True
    assert report["current_database_matches_bundle"] is True
    assert report["database_stable_during_handoff"] is True
    assert report["handoff_read_only"] is True
    assert report["requires_separate_next_action_authorization"] is True
    assert report["requires_fresh_operator_preflight_for_future_sequence"] is True
    assert report["future_checkpoint_refresh_authorized"] is False
    assert report["paper_supervisor_tick_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["phase8_promotion_authorized"] is False
    MODULE.validate_phase8_recursive_reentry_checkpoint_v25_evidence_handoff(
        report
    )


def test_mixed_bundle_and_post_audit_fail_closed(monkeypatch):
    temp, _, _, bundle, _, bundle_path, _, run = _build(monkeypatch)
    bundle["post_audit_sha256"] = "9" * 64
    _write(bundle_path, bundle)
    try:
        with pytest.raises(ValueError, match="bundle/post-audit digest mismatch"):
            run()
    finally:
        temp.cleanup()


def test_bundle_audit_database_binding_mismatch_fails_closed(monkeypatch):
    temp, _, _, bundle, _, bundle_path, _, run = _build(monkeypatch)
    bundle["current_database_sha256"] = "0" * 64
    _write(bundle_path, bundle)
    try:
        with pytest.raises(
            ValueError,
            match="final database binding mismatch",
        ):
            run()
    finally:
        temp.cleanup()


def test_current_database_drift_fails_closed(monkeypatch):
    temp, _, database, _, _, _, _, run = _build(monkeypatch)
    database.write_bytes(b"drifted-production-db")
    try:
        with pytest.raises(
            ValueError,
            match="no longer matches sealed v25 evidence",
        ):
            run()
    finally:
        temp.cleanup()


def test_database_change_during_handoff_fails_closed(monkeypatch):
    temp, _, database, bundle, _, _, _, run = _build(monkeypatch)
    stable = {
        "database": bundle["current_database_sha256"],
        "wal": None,
        "shm": None,
    }
    changed = {
        "database": "0" * 64,
        "wal": None,
        "shm": None,
    }
    states = iter((stable, changed))
    monkeypatch.setattr(MODULE, "_database_state", lambda path: next(states))
    try:
        with pytest.raises(
            ValueError,
            match="changed during v25 handoff",
        ):
            run()
    finally:
        temp.cleanup()


def test_symlinked_bundle_is_rejected(monkeypatch):
    temp, production, _, _, _, bundle_path, audit_path, _ = _build(monkeypatch)
    actual = bundle_path.with_name("bundle-actual.json")
    actual.write_bytes(bundle_path.read_bytes())
    bundle_path.unlink()
    bundle_path.symlink_to(actual)
    try:
        with pytest.raises(ValueError, match="must not be a symlink"):
            MODULE.build_phase8_recursive_reentry_checkpoint_v25_evidence_handoff(
                repository=production,
                source_tree=ROOT,
                evidence_bundle_path=bundle_path,
                post_audit_path=audit_path,
            )
    finally:
        temp.cleanup()


def test_symlinked_database_is_rejected(monkeypatch):
    temp, production, database, _, _, bundle_path, audit_path, _ = _build(monkeypatch)
    actual = database.with_name("real.db")
    actual.write_bytes(database.read_bytes())
    database.unlink()
    database.symlink_to(actual)
    try:
        with pytest.raises(ValueError, match="unsafe Pio database state file"):
            MODULE.build_phase8_recursive_reentry_checkpoint_v25_evidence_handoff(
                repository=production,
                source_tree=ROOT,
                evidence_bundle_path=bundle_path,
                post_audit_path=audit_path,
            )
    finally:
        temp.cleanup()


def test_resealed_handoff_cannot_authorize_future_checkpoint(monkeypatch):
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
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_evidence_handoff(
            report
        )


def test_resealed_handoff_cannot_authorize_live_submit(monkeypatch):
    temp, _, _, _, _, _, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["live_submit_authorized"] = True
    _reseal(report)
    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_evidence_handoff(
            report
        )


def test_handoff_has_no_execution_or_database_mutation_primitive():
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
    assert '"future_checkpoint_refresh_authorized": False' in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"phase8_promotion_authorized": False' in source
