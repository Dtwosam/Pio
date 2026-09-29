from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase7_controlled_live_exit_final_state_post_apply.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_final_state_post_apply",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


POSITION = "33333333333333333333333333333333"
POOL = "11111111111111111111111111111111"
OPENED = "11111111-2222-4333-8444-555555555555"
EXIT = "01234567-89ab-4def-8123-456789abcdef"
SETTLEMENT = "PHASE7_EXIT_SETTLEMENT:" + "c" * 64


class _FakeApplyModule:
    @staticmethod
    def validate_exit_final_reconciliation_apply(value):
        assert isinstance(value, dict)


class _Audit:
    def to_record(self):
        return {"clean": True}


class _FakeStorage:
    def __init__(self, path):
        self.path = Path(path)


class _FakePlanner:
    db_sha = "b" * 64

    @staticmethod
    def _load_json(path, *, label):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def _database_state(cls, path):
        return {"database": cls.db_sha, "wal": None, "shm": None}

    @staticmethod
    def _target_state(database, **kwargs):
        return {"position": "CLOSED", "target": "exact"}

    @staticmethod
    def _sha256_value(value):
        return "d" * 64

    @staticmethod
    def _backup_database(source, destination):
        shutil.copyfile(source, destination)

    @staticmethod
    def _load_reviewed(source):
        runtime = {
            "Storage": _FakeStorage,
            "audit_execution_receipts": lambda storage: _Audit(),
            "audit_live_execution_ledger": lambda storage: _Audit(),
        }
        return object(), object(), object(), runtime


def _apply(database: Path) -> dict:
    return {
        "apply_sha256": "a" * 64,
        "saved_plan_sha256": "1" * 64,
        "opened_decision_id": OPENED,
        "principal_exit_decision_id": EXIT,
        "settlement_decision_id": SETTLEMENT,
        "pool_address": POOL,
        "position_address": POSITION,
        "principal_signature": "principal-sig",
        "settlement_signature": "settlement-sig",
        "pio_database_path": str(database),
        "expected_target_state_sha256": "d" * 64,
        "target_state_matches_plan": True,
        "closure_proof_present_after_apply": True,
        "position_outcome_present_after_apply": True,
        "reconciliation_apply_complete": True,
        "reconciliation_remaining": False,
        "requires_post_apply_audit": True,
        "requires_learning_label_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "position_status_after_apply": "CLOSED",
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_repository_git_mutated": False,
    }


def _lifecycle(label_status: str) -> dict:
    return {
        "principal_chain_snapshot_present": True,
        "principal_execution_receipt_present": True,
        "principal_execution_effect_present": True,
        "principal_exit_event_present": True,
        "settlement_chain_snapshot_present": True,
        "settlement_execution_receipt_present": True,
        "settlement_execution_effect_present": True,
        "closure_proof_present": True,
        "close_event_present": True,
        "position_outcome_present": True,
        "position_status": "CLOSED",
        "principal_exit_next_status": "LIQUIDITY_REMOVED",
        "close_prior_status": "LIQUIDITY_REMOVED",
        "close_next_status": "CLOSED",
        "outcome_label_status": label_status,
        "position_outcome": {
            "position_address": POSITION,
            "pool_address": POOL,
            "opened_decision_id": OPENED,
            "closed_decision_id": SETTLEMENT,
            "execution_count": 3,
            "token_x_wallet_delta_atomic": 10,
            "token_y_wallet_delta_atomic": 20,
            "composition_fee_x_atomic": 0,
            "composition_fee_y_atomic": 0,
            "earned_fee_x_atomic": 1,
            "earned_fee_y_atomic": 2,
            "reward_one_atomic": 3,
            "reward_two_atomic": 0,
            "network_fee_lamports": 31,
            "label_status": label_status,
        },
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, *, label_status: str = "ATOMIC_ONLY"):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production-db")
        applied = _apply(database)
        applied_path = _write(root / "apply.json", applied)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeApplyModule, _FakePlanner),
        )
        monkeypatch.setattr(
            MODULE,
            "_closed_lifecycle_state",
            lambda *args, **kwargs: _lifecycle(label_status),
        )

        report = MODULE.build_exit_final_post_apply_audit(
            source_tree=ROOT,
            saved_reconciliation_apply_path=applied_path,
            expected_reconciliation_apply_sha256=applied["apply_sha256"],
            pio_database_path=database,
        )
        return report


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_atomic_only_outcome_routes_to_valuation(monkeypatch):
    report = _build(monkeypatch, label_status="ATOMIC_ONLY")

    assert report["position_status"] == "CLOSED"
    assert report["principal_exit_next_status"] == "LIQUIDITY_REMOVED"
    assert report["close_prior_status"] == "LIQUIDITY_REMOVED"
    assert report["close_next_status"] == "CLOSED"
    assert report["exact_exit_lifecycle_reconciled"] is True
    assert report["live_position_valuation_required"] is True
    assert report["live_position_valuation_complete"] is False
    assert report["learning_label_reconciliation_required"] is True
    assert report["learning_label_reconciliation_ready"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["new_live_capital_authorized"] is False
    assert report["phase7_promotion_authorized"] is False
    assert report["production_pio_database_modified"] is False


def test_valued_outcome_routes_to_learning_label(monkeypatch):
    report = _build(monkeypatch, label_status="VALUED")

    assert report["live_position_valuation_required"] is False
    assert report["live_position_valuation_complete"] is True
    assert report["learning_label_reconciliation_required"] is True
    assert report["learning_label_reconciliation_ready"] is True
    assert report["phase7_evidence_status_recheck_required"] is True
    assert report["requires_separate_phase7_promotion_action"] is True


def test_apply_target_digest_must_match(monkeypatch):
    original = _FakePlanner._sha256_value
    monkeypatch.setattr(
        _FakePlanner,
        "_sha256_value",
        staticmethod(lambda value: "f" * 64),
    )
    try:
        with pytest.raises(
            ValueError,
            match="target differs from apply artifact",
        ):
            _build(monkeypatch)
    finally:
        monkeypatch.setattr(
            _FakePlanner,
            "_sha256_value",
            original,
        )


def test_apply_must_be_complete(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production-db")
        applied = _apply(database)
        applied["reconciliation_apply_complete"] = False
        applied_path = _write(root / "apply.json", applied)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeApplyModule, _FakePlanner),
        )

        with pytest.raises(
            ValueError,
            match="reconciliation_apply_complete=true",
        ):
            MODULE.build_exit_final_post_apply_audit(
                source_tree=ROOT,
                saved_reconciliation_apply_path=applied_path,
                expected_reconciliation_apply_sha256=applied["apply_sha256"],
                pio_database_path=database,
            )


def test_resealed_audit_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_final_post_apply_audit(report)


def test_resealed_audit_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_final_post_apply_audit(report)


def test_post_apply_audit_has_no_mutation_or_submission_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "_backup_database(database, private_db)" in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "UPDATE live_positions" not in source
    assert "INSERT INTO live_position" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
    assert '"production_pio_database_modified": False' in source
