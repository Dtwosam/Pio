from __future__ import annotations

import copy
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
    / "check_phase7_controlled_live_post_reconciliation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_post_reconciliation",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


DECISION_ID = "11111111-2222-4333-8444-555555555555"
SIGNATURE = "sig-1"
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"


def _sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _target(status):
    if status == "CONFIRMED":
        return {
            "live_position": [
                {
                    "position_address": POSITION,
                    "pool_address": POOL,
                    "status": "OPEN",
                    "opened_decision_id": DECISION_ID,
                }
            ],
            "live_position_event": [
                {
                    "decision_id": DECISION_ID,
                    "position_address": POSITION,
                    "action": "ENTER",
                    "next_status": "OPEN",
                }
            ],
        }
    return {
        "live_position": [],
        "live_position_event": [],
    }


def _apply(status):
    target = _target(status)
    return {
        "apply_sha256": "a" * 64,
        "reconciliation_apply_complete": True,
        "reconciliation_remaining": False,
        "target_state_matches_plan": True,
        "pio_database_path": "",
        "expected_target_state_sha256": _sha(target),
        "expected_position_address": POSITION if status == "CONFIRMED" else None,
        "intent_status": status,
        "decision_id": DECISION_ID,
        "signature": SIGNATURE,
        "pool_address": POOL,
        "saved_plan_sha256": "b" * 64,
    }


class _Audit:
    def __init__(self, clean):
        self.clean = clean

    def to_record(self):
        return {"clean": self.clean}


def _build(
    monkeypatch,
    *,
    status="CONFIRMED",
    target_drift=False,
    receipt_clean=True,
    ledger_clean=True,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"db")
        applied = _apply(status)
        applied["pio_database_path"] = str(database)
        path = root / "apply.json"
        path.write_text(json.dumps(applied), encoding="utf-8")

        target = _target(status)

        class FakeApply:
            @staticmethod
            def validate_reconciliation_apply(value):
                assert isinstance(value, dict)

        class FakeStorage:
            def __init__(self, path):
                self.path = Path(path)

        runtime = {
            "Storage": FakeStorage,
            "audit_execution_receipts": lambda storage: _Audit(receipt_clean),
            "audit_live_execution_ledger": lambda storage: _Audit(ledger_clean),
        }

        class FakePlanner:
            @staticmethod
            def _load_json(path_value, *, label):
                return json.loads(Path(path_value).read_text(encoding="utf-8"))

            @staticmethod
            def _database_state(path_value):
                return {"database": "1" * 64, "wal": None, "shm": None}

            @staticmethod
            def _pre_state(database_value, *, decision_id, signature):
                return {
                    "chain": True,
                    "receipt": True,
                    "effect": True,
                    "position_event": status == "CONFIRMED",
                }

            @staticmethod
            def _target_state(*args, **kwargs):
                if target_drift:
                    return {
                        "live_position": [],
                        "live_position_event": [],
                        "drift": True,
                    }
                return copy.deepcopy(target)

            @staticmethod
            def _sha256_value(value):
                return _sha(value)

            @staticmethod
            def _backup_database(source, destination):
                destination.write_bytes(b"private")

            @staticmethod
            def _load_reviewed_runtime(source):
                return object(), runtime

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (FakeApply, FakePlanner),
        )

        report = MODULE.build_post_reconciliation_audit(
            source_tree=ROOT,
            saved_reconciliation_apply_path=path,
            expected_reconciliation_apply_sha256=applied["apply_sha256"],
            pio_database_path=database,
        )
        return report


def _reseal(report):
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_confirmed_enter_requires_open_position_followup(monkeypatch):
    report = _build(monkeypatch, status="CONFIRMED")

    assert report["exact_execution_state_reconciled"] is True
    assert report["position_event_present"] is True
    assert report["position_address"] == POSITION
    assert report["position_status"] == "OPEN"
    assert report["learning_label_reconciliation_required"] is True
    assert report["learning_label_reconciliation_complete"] is False
    assert report["open_position_lifecycle_followup_required"] is True
    assert report["phase7_evidence_status_recheck_required"] is True
    assert report["new_live_entry_authorized"] is False
    assert report["new_live_capital_authorized"] is False


def test_failed_enter_has_no_position_or_label_followup(monkeypatch):
    report = _build(monkeypatch, status="FAILED")

    assert report["position_event_present"] is False
    assert report["position_address"] is None
    assert report["position_status"] is None
    assert report["learning_label_reconciliation_required"] is False
    assert report["learning_label_reconciliation_complete"] is True
    assert report["open_position_lifecycle_followup_required"] is False
    assert report["phase7_promotion_authorized"] is False


def test_global_audit_debt_is_reported_without_faking_exact_state_failure(
    monkeypatch,
):
    report = _build(
        monkeypatch,
        receipt_clean=False,
        ledger_clean=False,
    )

    assert report["post_reconciliation_audit_ready"] is True
    assert report["global_receipt_audit_clean"] is False
    assert report["global_live_ledger_clean"] is False
    assert report["new_live_entry_authorized"] is False


def test_target_state_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="production target state differs"):
        _build(monkeypatch, target_drift=True)


def test_confirmed_target_requires_open_position():
    target = _target("CONFIRMED")
    target["live_position"][0]["status"] = "LIQUIDITY_REMOVED"

    with pytest.raises(ValueError, match="must be OPEN"):
        MODULE._position_from_target(
            target,
            intent_status="CONFIRMED",
            decision_id=DECISION_ID,
            pool_address=POOL,
            expected_position_address=POSITION,
        )


def test_resealed_audit_cannot_authorize_new_entry(monkeypatch):
    report = _build(monkeypatch)
    report["new_live_entry_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="new_live_entry_authorized=false"):
        MODULE.validate_post_reconciliation_audit(report)


def test_resealed_audit_cannot_authorize_new_live_capital(monkeypatch):
    report = _build(monkeypatch)
    report["new_live_capital_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="new_live_capital_authorized=false"):
        MODULE.validate_post_reconciliation_audit(report)


def test_resealed_audit_cannot_authorize_phase7_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase7_promotion_authorized=false"):
        MODULE.validate_post_reconciliation_audit(report)


def test_post_reconciliation_tool_is_read_only():
    source = TOOL.read_text(encoding="utf-8")

    assert '"controlled-live-submit-once"' not in source
    assert '"controlled-live-submit"' not in source
    assert '"execution-confirmation"' not in source
    assert '"execution-recovery"' not in source
    assert "apply_live_execution_effect(" not in source
    assert "apply_live_position_effect(" not in source
    assert '"production_pio_database_modified": False' in source
    assert '"new_live_entry_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
