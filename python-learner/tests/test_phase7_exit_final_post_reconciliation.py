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
    / "check_phase7_controlled_live_exit_final_post_reconciliation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_final_post_reconciliation",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


POSITION = "33333333333333333333333333333333"
POOL = "11111111111111111111111111111111"
SETTLEMENT_DECISION_ID = "PHASE7_EXIT_SETTLEMENT:" + "c" * 64


class _Audit:
    def __init__(self, clean: bool):
        self.clean = clean

    def to_record(self):
        return {"clean": self.clean}


class _FakeStorage:
    pass


class _FakePlanner:
    applied = None
    database_digest = "d" * 64
    target_digest = "3" * 64
    receipt_clean = True
    live_clean = True

    @classmethod
    def _load_json(cls, path, *, label):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def _database_state(cls, database):
        return {
            "database": cls.database_digest,
            "wal": None,
            "shm": None,
        }

    @classmethod
    def _target_state(cls, *args, **kwargs):
        return {"position": "CLOSED", "outcome": "ATOMIC_ONLY"}

    @classmethod
    def _sha256_value(cls, value):
        return cls.target_digest

    @classmethod
    def _load_reviewed(cls, source):
        runtime = {
            "Storage": _FakeStorage,
            "audit_execution_receipts": (
                lambda storage: _Audit(cls.receipt_clean)
            ),
            "audit_live_execution_ledger": (
                lambda storage: _Audit(cls.live_clean)
            ),
        }
        return object(), object(), object(), runtime


class _FakeApply:
    @staticmethod
    def _load_planner(source):
        return _FakePlanner

    @staticmethod
    def validate_exit_final_reconciliation_apply(value):
        assert isinstance(value, dict)

    @staticmethod
    def _position_state(
        database,
        *,
        position_address,
        settlement_decision_id,
    ):
        assert position_address == POSITION
        assert settlement_decision_id == SETTLEMENT_DECISION_ID
        outcome = {
            "position_address": POSITION,
            "pool_address": POOL,
            "opened_decision_id": "open",
            "closed_decision_id": SETTLEMENT_DECISION_ID,
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
            "label_status": "ATOMIC_ONLY",
        }
        return "CLOSED", True, True, outcome


def _applied(database: Path) -> dict:
    return {
        "apply_sha256": "a" * 64,
        "saved_plan_sha256": "b" * 64,
        "opened_decision_id": "11111111-2222-4333-8444-555555555555",
        "principal_exit_decision_id": (
            "01234567-89ab-4def-8123-456789abcdef"
        ),
        "settlement_decision_id": SETTLEMENT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "principal_signature": "principal-sig",
        "settlement_signature": "settlement-sig",
        "pio_database_path": str(database),
        "pio_database_sha256_after": "d" * 64,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
        "expected_target_state_sha256": "3" * 64,
        "target_state_matches_plan": True,
        "closure_proof_present_after_apply": True,
        "position_outcome_present_after_apply": True,
        "reconciliation_apply_complete": True,
        "reconciliation_remaining": False,
        "requires_post_apply_audit": True,
        "requires_learning_label_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "position_status_after_apply": "CLOSED",
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    database_digest: str = "d" * 64,
    target_digest: str = "3" * 64,
    receipt_clean: bool = True,
    live_clean: bool = True,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production")
        applied = _applied(database)
        apply_path = _write(root / "apply.json", applied)

        _FakePlanner.database_digest = database_digest
        _FakePlanner.target_digest = target_digest
        _FakePlanner.receipt_clean = receipt_clean
        _FakePlanner.live_clean = live_clean

        monkeypatch.setattr(
            MODULE,
            "_load_apply",
            lambda source: _FakeApply,
        )

        report = MODULE.build_exit_final_post_reconciliation_audit(
            source_tree=ROOT,
            saved_apply_path=apply_path,
            expected_apply_sha256=applied["apply_sha256"],
            pio_database_path=database,
        )
        return report


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_apply_dependency_is_exactly_pinned():
    path = ROOT / MODULE.APPLY_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.APPLY_TOOL
    ]


def test_post_reconciliation_audit_proves_closed_clean_lifecycle(
    monkeypatch,
):
    report = _build(monkeypatch)

    assert report["target_state_matches_apply"] is True
    assert report["position_status"] == "CLOSED"
    assert report["closure_proof_present"] is True
    assert report["position_outcome_present"] is True
    assert report["global_receipt_audit_clean"] is True
    assert report["global_live_ledger_clean"] is True
    assert report["exact_exit_lifecycle_reconciled"] is True
    assert report["learning_label_reconciliation_required"] is True
    assert report["phase7_evidence_status_recheck_required"] is True
    assert report["open_position_lifecycle_followup_required"] is False
    assert report["requires_separate_phase7_promotion_action"] is True
    assert report["transaction_submission_authorized"] is False
    assert report["new_live_capital_authorized"] is False
    assert report["phase7_promotion_authorized"] is False
    assert report["production_pio_database_modified"] is False


def test_database_drift_after_apply_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="changed after final reconciliation apply"):
        _build(
            monkeypatch,
            database_digest="f" * 64,
        )


def test_target_state_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="production target state differs",
    ):
        _build(
            monkeypatch,
            target_digest="f" * 64,
        )


def test_dirty_receipt_audit_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="execution-receipt audit is not clean",
    ):
        _build(
            monkeypatch,
            receipt_clean=False,
        )


def test_dirty_live_ledger_audit_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="live-ledger audit is not clean",
    ):
        _build(
            monkeypatch,
            live_clean=False,
        )


def test_resealed_audit_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_final_post_reconciliation_audit(report)


def test_resealed_audit_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_final_post_reconciliation_audit(report)


def test_post_reconciliation_audit_is_read_only():
    source = TOOL.read_text(encoding="utf-8")

    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "UPDATE live_positions" not in source
    assert "INSERT INTO live_" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
    assert '"production_pio_database_modified": False' in source
