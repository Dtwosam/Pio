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
    / "build_phase8_recursive_reentry_continuation_checkpoint_v25.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_phase8_recursive_reentry_continuation_checkpoint_v25",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _FakeBundle:
    @staticmethod
    def validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_bundle(
        value,
    ):
        assert isinstance(value, dict)


class _FakeAudit:
    @staticmethod
    def validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_post_audit(
        value,
    ):
        assert isinstance(value, dict)


def _lineage() -> dict:
    return {
        "source_pair_entry_post_audit_sha256": "c" * 64,
        "pair_entry_request_sha256": "d" * 64,
        "pair_entry_input_verification_sha256": "e" * 64,
        "source_final_evaluation_sha256": "f" * 64,
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
    }


def _audit(route: str = "continue") -> dict:
    continue_ready = route == "continue"
    terminal_ready = route == "terminal"
    recovery_ready = route == "recovery"
    lineage = _lineage()
    return {
        "post_audit_sha256": "a" * 64,
        "execution_receipt_sha256": "b" * 64,
        "source_checkpoint_sha256": "9" * 64,
        "source_post_audit_sha256": "8" * 64,
        "source_execution_receipt_sha256": "7" * 64,
        "source_pair_entry_post_audit_sha256": lineage[
            "source_pair_entry_post_audit_sha256"
        ],
        "pair_entry_request_sha256": lineage["pair_entry_request_sha256"],
        "pair_entry_input_verification_sha256": lineage[
            "pair_entry_input_verification_sha256"
        ],
        "pair_lineage_sha256": hashlib.sha256(
            MODULE._canonical_bytes(lineage)
        ).hexdigest(),
        "latest_previous_tick_post_audit_sha256": "6" * 64,
        "source_final_evaluation_sha256": lineage[
            "source_final_evaluation_sha256"
        ],
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "audit_database_sha256_after": "1" * 64,
        "audit_wal_sha256_after": None,
        "audit_shm_sha256_after": None,
        "active_cycle_id": lineage["active_cycle_id"],
        "incumbent_model_id": lineage["incumbent_model_id"],
        "challenger_model_id": lineage["challenger_model_id"],
        "account_id": lineage["account_id"],
        "previous_pair_id": lineage["previous_pair_id"],
        "pair_id": lineage["pair_id"],
        "pool_address": lineage["pool_address"],
        "entry_observed_at": lineage["entry_observed_at"],
        "incumbent_position_id": lineage["incumbent_position_id"],
        "challenger_position_id": lineage["challenger_position_id"],
        "requested_position_ids": [
            lineage["incumbent_position_id"],
            lineage["challenger_position_id"],
        ],
        "target_chain_observed_at": "2026-09-30T09:40:00+00:00",
        "evidence_cycle_id": "phase8-v24:pair-4:abc123",
        "expected_run_id": "run-1",
        "receipt_tick_status": "FAILED" if recovery_ready else "COMPLETE",
        "receipt_pair_tick_complete": not recovery_ready,
        "receipt_pair_tick_partial_failure": recovery_ready,
        "pair_both_open": continue_ready,
        "pair_any_closed": terminal_ready,
        "next_debt_type": (
            "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
            if continue_ready
            else (
                "PAPER_PAIR_TERMINAL_EVALUATION_REQUIRED"
                if terminal_ready
                else "PAPER_PAIR_TICK_RECOVERY_REQUIRED"
            )
        ),
        "next_scope": "challenger-1",
        "continuation_route": (
            "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V24_NEXT_EVIDENCE_TICK_REVIEW"
            if continue_ready
            else (
                "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V24_TERMINAL_EVALUATION_REVIEW"
                if terminal_ready
                else "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V24_TICK_RECOVERY_REVIEW"
            )
        ),
        "next_evidence_tick_review_ready": continue_ready,
        "terminal_pair_evaluation_ready": terminal_ready,
        "tick_recovery_review_ready": recovery_ready,
        "separate_next_action_authorization_required": True,
        "post_tick_audit_ready": True,
    }


def _bundle(audit: dict) -> dict:
    return {
        "bundle_sha256": "0" * 64,
        "post_audit_sha256": audit["post_audit_sha256"],
        "execution_receipt_sha256": audit["execution_receipt_sha256"],
        "production_repository": audit["production_repository"],
        "pio_database_path": audit["pio_database_path"],
        "current_database_sha256": audit["audit_database_sha256_after"],
        "current_wal_sha256": audit["audit_wal_sha256_after"],
        "current_shm_sha256": audit["audit_shm_sha256_after"],
        "active_cycle_id": audit["active_cycle_id"],
        "incumbent_model_id": audit["incumbent_model_id"],
        "challenger_model_id": audit["challenger_model_id"],
        "account_id": audit["account_id"],
        "previous_pair_id": audit["previous_pair_id"],
        "pair_id": audit["pair_id"],
        "pool_address": audit["pool_address"],
        "entry_observed_at": audit["entry_observed_at"],
        "incumbent_position_id": audit["incumbent_position_id"],
        "challenger_position_id": audit["challenger_position_id"],
        "requested_position_ids": list(audit["requested_position_ids"]),
        "evidence_cycle_id": audit["evidence_cycle_id"],
        "expected_run_id": audit["expected_run_id"],
        "target_chain_observed_at": audit["target_chain_observed_at"],
        "receipt_tick_status": audit["receipt_tick_status"],
        "receipt_pair_tick_complete": audit["receipt_pair_tick_complete"],
        "receipt_pair_tick_partial_failure": audit[
            "receipt_pair_tick_partial_failure"
        ],
        "pair_both_open": audit["pair_both_open"],
        "pair_any_closed": audit["pair_any_closed"],
        "next_debt_type": audit["next_debt_type"],
        "next_scope": audit["next_scope"],
        "continuation_route": audit["continuation_route"],
        "next_evidence_tick_review_ready": audit[
            "next_evidence_tick_review_ready"
        ],
        "tick_recovery_review_ready": audit["tick_recovery_review_ready"],
        "terminal_pair_evaluation_ready": audit[
            "terminal_pair_evaluation_ready"
        ],
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
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    route: str = "continue",
    audit_mutator=None,
    bundle_mutator=None,
):
    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (_FakeBundle, _FakeAudit),
    )
    temp = tempfile.TemporaryDirectory()
    audit = _audit(route)
    bundle = _bundle(audit)
    if audit_mutator is not None:
        audit_mutator(audit)
    if bundle_mutator is not None:
        bundle_mutator(bundle)
    root = Path(temp.name)
    audit_path = _write(root / "post-audit.json", audit)
    bundle_path = _write(root / "evidence-bundle.json", bundle)

    def run():
        return MODULE.build_phase8_recursive_reentry_continuation_checkpoint_v25(
            source_tree=ROOT,
            evidence_bundle_path=bundle_path,
            post_audit_path=audit_path,
        )

    return temp, run


def _reseal(checkpoint: dict) -> None:
    identity = {field: checkpoint[field] for field in MODULE.CHECKPOINT_FIELDS}
    checkpoint["checkpoint_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_bundle_and_post_audit_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_reviewed_dependencies_export_expected_v24_validators():
    bundle_module, audit_module = MODULE._load_reviewed(ROOT)
    assert hasattr(
        bundle_module,
        "validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_bundle",
    )
    assert hasattr(
        audit_module,
        "validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_post_audit",
    )


def test_continue_checkpoint_requires_sealed_bundle(monkeypatch):
    temp, run = _build(monkeypatch, route="continue")
    try:
        checkpoint = run()
    finally:
        temp.cleanup()

    assert checkpoint["format_version"] == 25
    assert checkpoint["checkpoint_state"] == MODULE.STATE_CONTINUE
    assert checkpoint["source_evidence_bundle_sha256"] == "0" * 64
    assert checkpoint["source_post_audit_sha256"] == "a" * 64
    assert checkpoint["source_execution_receipt_sha256"] == "b" * 64
    assert checkpoint["pio_database_sha256"] == "1" * 64
    assert checkpoint["continuation_review_ready"] is True
    assert checkpoint["terminal_review_ready"] is False
    assert checkpoint["recovery_review_ready"] is False
    assert checkpoint["paper_supervisor_tick_authorized"] is False
    assert checkpoint["paper_trading_authorized"] is False
    assert checkpoint["live_submit_authorized"] is False
    assert checkpoint["continuous_promotion_authorized"] is False


def test_terminal_bundle_routes_terminal(monkeypatch):
    temp, run = _build(monkeypatch, route="terminal")
    try:
        checkpoint = run()
    finally:
        temp.cleanup()

    assert checkpoint["checkpoint_state"] == MODULE.STATE_TERMINAL
    assert checkpoint["terminal_review_ready"] is True
    assert checkpoint["continuation_review_ready"] is False
    assert checkpoint["recovery_review_ready"] is False
    assert checkpoint["pair_any_closed"] is True


def test_recovery_bundle_routes_recovery(monkeypatch):
    temp, run = _build(monkeypatch, route="recovery")
    try:
        checkpoint = run()
    finally:
        temp.cleanup()

    assert checkpoint["checkpoint_state"] == MODULE.STATE_RECOVERY
    assert checkpoint["recovery_review_ready"] is True
    assert checkpoint["continuation_review_ready"] is False
    assert checkpoint["terminal_review_ready"] is False


def test_bundle_post_audit_digest_mismatch_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        bundle_mutator=lambda value: value.update(
            post_audit_sha256="9" * 64
        ),
    )
    try:
        with pytest.raises(
            ValueError,
            match="bundle/post-audit digest mismatch",
        ):
            run()
    finally:
        temp.cleanup()


def test_bundle_execution_receipt_mismatch_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        bundle_mutator=lambda value: value.update(
            execution_receipt_sha256="9" * 64
        ),
    )
    try:
        with pytest.raises(
            ValueError,
            match="bundle/audit execution_receipt_sha256 mismatch",
        ):
            run()
    finally:
        temp.cleanup()


def test_bundle_pair_binding_mismatch_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        bundle_mutator=lambda value: value.update(pair_id="pair-999"),
    )
    try:
        with pytest.raises(
            ValueError,
            match="bundle/audit pair_id mismatch",
        ):
            run()
    finally:
        temp.cleanup()


def test_bundle_final_database_mismatch_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        bundle_mutator=lambda value: value.update(
            current_database_sha256="9" * 64
        ),
    )
    try:
        with pytest.raises(
            ValueError,
            match="final database binding mismatch",
        ):
            run()
    finally:
        temp.cleanup()


def test_bundle_authority_escalation_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        bundle_mutator=lambda value: value.update(
            live_submit_authorized=True
        ),
    )
    try:
        with pytest.raises(
            ValueError,
            match="refuses bundle live_submit_authorized=true",
        ):
            run()
    finally:
        temp.cleanup()


def test_resealed_checkpoint_requires_valid_bundle_digest(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        checkpoint = run()
    finally:
        temp.cleanup()

    checkpoint["source_evidence_bundle_sha256"] = "not-a-digest"
    _reseal(checkpoint)
    with pytest.raises(
        ValueError,
        match="source_evidence_bundle_sha256 is invalid",
    ):
        MODULE.validate_phase8_recursive_reentry_continuation_checkpoint_v25(
            checkpoint
        )


def test_resealed_checkpoint_cannot_authorize_tick(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        checkpoint = run()
    finally:
        temp.cleanup()

    checkpoint["paper_supervisor_tick_authorized"] = True
    _reseal(checkpoint)
    with pytest.raises(
        ValueError,
        match="paper_supervisor_tick_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_continuation_checkpoint_v25(
            checkpoint
        )


def test_resealed_checkpoint_cannot_authorize_promotion(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        checkpoint = run()
    finally:
        temp.cleanup()

    checkpoint["phase8_promotion_authorized"] = True
    _reseal(checkpoint)
    with pytest.raises(
        ValueError,
        match="phase8_promotion_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_continuation_checkpoint_v25(
            checkpoint
        )


def test_checkpoint_v25_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
