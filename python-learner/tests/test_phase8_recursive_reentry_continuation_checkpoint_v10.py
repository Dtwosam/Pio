from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "build_phase8_recursive_reentry_continuation_checkpoint_v10.py"

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_recursive_reentry_continuation_checkpoint_v10",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _FakeAudit:
    @staticmethod
    def validate_phase8_recursive_reentry_checkpoint_v9_continuation_evidence_tick_post_audit(
        value,
    ):
        assert isinstance(value, dict)


def _audit(route: str = "continue") -> dict:
    continue_ready = route == "continue"
    terminal_ready = route == "terminal"
    recovery_ready = route == "recovery"
    lineage = {
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
    return {
        "post_audit_sha256": "a" * 64,
        "execution_receipt_sha256": "b" * 64,
        "source_checkpoint_sha256": "9" * 64,
        "source_post_audit_sha256": "8" * 64,
        "source_execution_receipt_sha256": "7" * 64,
        "source_pair_entry_post_audit_sha256": "c" * 64,
        "pair_entry_request_sha256": "d" * 64,
        "pair_entry_input_verification_sha256": "e" * 64,
        "pair_lineage_sha256": hashlib.sha256(
            MODULE._canonical_bytes(lineage)
        ).hexdigest(),
        "latest_previous_tick_post_audit_sha256": "6" * 64,
        "source_final_evaluation_sha256": "f" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "audit_database_sha256_after": "1" * 64,
        "audit_wal_sha256_after": None,
        "audit_shm_sha256_after": None,
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
        "target_chain_observed_at": "2026-09-30T09:40:00+00:00",
        "evidence_cycle_id": "phase8-recursive-reentry-checkpoint-continuation:pair-4:abc123",
        "expected_run_id": "run-1",
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
            "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_NEXT_EVIDENCE_TICK_REVIEW"
            if continue_ready
            else (
                "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_TERMINAL_EVALUATION_REVIEW"
                if terminal_ready
                else "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_TICK_RECOVERY_REVIEW"
            )
        ),
        "next_evidence_tick_review_ready": continue_ready,
        "terminal_pair_evaluation_ready": terminal_ready,
        "tick_recovery_review_ready": recovery_ready,
        "separate_next_action_authorization_required": True,
        "post_tick_audit_ready": True,
    }

def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, *, route: str = "continue", mutator=None):
    monkeypatch.setattr(MODULE, "_load_post_audit", lambda source: _FakeAudit)
    temp = tempfile.TemporaryDirectory()
    value = _audit(route)
    if mutator is not None:
        mutator(value)
    path = _write(Path(temp.name) / "audit.json", value)

    def run():
        return MODULE.build_phase8_recursive_reentry_continuation_checkpoint_v10(
            source_tree=ROOT,
            post_audit_path=path,
        )

    return temp, run


def _reseal(checkpoint: dict) -> None:
    identity = {
        field: checkpoint[field]
        for field in MODULE.CHECKPOINT_FIELDS
    }
    checkpoint["checkpoint_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_post_audit_is_exactly_pinned():
    path = ROOT / MODULE.POST_AUDIT_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.POST_AUDIT_TOOL
    ]


def test_reviewed_post_audit_exports_expected_v9_validator():
    module = MODULE._load_post_audit(ROOT)
    assert hasattr(
        module,
        "validate_phase8_recursive_reentry_checkpoint_v9_continuation_evidence_tick_post_audit",
    )


def test_continue_checkpoint_preserves_pair_lineage(monkeypatch):
    temp, run = _build(monkeypatch, route="continue")
    try:
        checkpoint = run()
    finally:
        temp.cleanup()

    assert checkpoint["checkpoint_state"] == MODULE.STATE_CONTINUE
    assert checkpoint["continuation_review_ready"] is True
    assert checkpoint["terminal_review_ready"] is False
    assert checkpoint["recovery_review_ready"] is False
    assert checkpoint["previous_pair_id"] == "pair-3"
    assert checkpoint["pair_id"] == "pair-4"
    assert checkpoint["source_pair_entry_post_audit_sha256"] == "c" * 64
    assert checkpoint["pair_entry_request_sha256"] == "d" * 64
    assert checkpoint["pair_entry_input_verification_sha256"] == "e" * 64
    assert checkpoint["pair_lineage"]["pair_id"] == "pair-4"
    assert checkpoint["pair_lineage"]["source_pair_entry_post_audit_sha256"] == "c" * 64
    assert checkpoint["pair_lineage"]["pair_entry_request_sha256"] == "d" * 64
    assert checkpoint["pair_lineage"]["pair_entry_input_verification_sha256"] == "e" * 64
    assert checkpoint["latest_previous_tick_post_audit_sha256"] == "a" * 64
    assert checkpoint["latest_tick_observed_at"] == "2026-09-30T09:40:00+00:00"
    assert checkpoint["paper_supervisor_tick_authorized"] is False
    assert checkpoint["paper_trading_authorized"] is False
    assert checkpoint["continuous_promotion_authorized"] is False
    assert checkpoint["live_submit_authorized"] is False


def test_terminal_checkpoint_routes_without_authority(monkeypatch):
    temp, run = _build(monkeypatch, route="terminal")
    try:
        checkpoint = run()
    finally:
        temp.cleanup()

    assert checkpoint["checkpoint_state"] == MODULE.STATE_TERMINAL
    assert checkpoint["continuation_review_ready"] is False
    assert checkpoint["terminal_review_ready"] is True
    assert checkpoint["recovery_review_ready"] is False
    assert checkpoint["pair_any_closed"] is True
    assert checkpoint["pair_both_open"] is False
    assert checkpoint["paper_supervisor_tick_authorized"] is False


def test_recovery_checkpoint_routes_without_authority(monkeypatch):
    temp, run = _build(monkeypatch, route="recovery")
    try:
        checkpoint = run()
    finally:
        temp.cleanup()

    assert checkpoint["checkpoint_state"] == MODULE.STATE_RECOVERY
    assert checkpoint["continuation_review_ready"] is False
    assert checkpoint["terminal_review_ready"] is False
    assert checkpoint["recovery_review_ready"] is True
    assert checkpoint["paper_evidence_collection_authorized"] is False


def test_ambiguous_post_audit_route_fails_closed(monkeypatch):
    def mutate(value):
        value["next_evidence_tick_review_ready"] = True
        value["terminal_pair_evaluation_ready"] = True

    temp, run = _build(monkeypatch, route="continue", mutator=mutate)
    try:
        with pytest.raises(ValueError, match="route is ambiguous"):
            run()
    finally:
        temp.cleanup()


def test_pair_id_regression_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        mutator=lambda value: value.update(pair_id="pair-3"),
    )
    try:
        with pytest.raises(ValueError, match="pair id was not advanced"):
            run()
    finally:
        temp.cleanup()


def test_post_audit_pair_lineage_digest_drift_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        mutator=lambda value: value.update(pair_lineage_sha256="0" * 64),
    )
    try:
        with pytest.raises(ValueError, match="post-audit pair lineage drifted"):
            run()
    finally:
        temp.cleanup()


def test_resealed_checkpoint_cannot_drift_pair_lineage(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        checkpoint = run()
    finally:
        temp.cleanup()

    checkpoint["pair_lineage"]["pair_id"] = "pair-99"
    _reseal(checkpoint)
    with pytest.raises(ValueError, match="pair lineage drifted"):
        MODULE.validate_phase8_recursive_reentry_continuation_checkpoint_v10(
            checkpoint
        )


def test_resealed_checkpoint_cannot_rewrite_entry_source(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        checkpoint = run()
    finally:
        temp.cleanup()

    checkpoint["pair_lineage"]["pair_entry_request_sha256"] = "9" * 64
    checkpoint["pair_lineage_sha256"] = MODULE._sha256_bytes(
        MODULE._canonical_bytes(checkpoint["pair_lineage"])
    )
    _reseal(checkpoint)
    with pytest.raises(ValueError, match="pair lineage drifted"):
        MODULE.validate_phase8_recursive_reentry_continuation_checkpoint_v10(
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
        MODULE.validate_phase8_recursive_reentry_continuation_checkpoint_v10(
            checkpoint
        )


def test_resealed_checkpoint_cannot_authorize_promotion(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        checkpoint = run()
    finally:
        temp.cleanup()

    checkpoint["continuous_promotion_authorized"] = True
    _reseal(checkpoint)
    with pytest.raises(
        ValueError,
        match="continuous_promotion_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_continuation_checkpoint_v10(
            checkpoint
        )


def test_checkpoint_v10_has_no_paper_or_live_mutation_primitive():
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
