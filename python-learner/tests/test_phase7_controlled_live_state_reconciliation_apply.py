from __future__ import annotations

import copy
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
    / "apply_phase7_controlled_live_state_reconciliation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "apply_phase7_controlled_live_state_reconciliation",
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
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _plan(*, status="CONFIRMED", actions=None):
    if actions is None:
        actions = [
            MODULE.ACTION_INGEST_CHAIN,
            MODULE.ACTION_INGEST_RECEIPT,
            MODULE.ACTION_APPLY_EFFECT,
            MODULE.ACTION_APPLY_POSITION,
        ]
    snapshot = {"signature": SIGNATURE, "slot": 123}
    target = {
        "decision_id": DECISION_ID,
        "position": POSITION if status == "CONFIRMED" else None,
    }
    return {
        "plan_sha256": "a" * 64,
        "reconciliation_plan_ready": True,
        "reconciliation_required": True,
        "requires_separate_reconciliation_apply": True,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_persisted": False,
        "production_pio_database_modified": False,
        "planned_actions": list(actions),
        "expected_position_address": POSITION if status == "CONFIRMED" else None,
        "private_target_state_sha256": _sha(target),
        "transaction_snapshot_sha256": _sha(snapshot),
        "decision_id": DECISION_ID,
        "signature": SIGNATURE,
        "pool_address": POOL,
        "intent_status": status,
        "observed_at": "2026-09-29T09:00:00+00:00",
        "pio_database_sha256_before": "1" * 64,
        "pio_wal_sha256_before": None,
        "pio_shm_sha256_before": None,
    }


def _receipt(*, status="CONFIRMED"):
    return {
        "receipt": {
            "decision_id": DECISION_ID,
            "signature": SIGNATURE,
            "mode": "LIVE",
            "action": "ENTER",
            "pool_address": POOL,
            "intent_status": status,
            "succeeded": status == "CONFIRMED",
        }
    }


class _Audit:
    def __init__(self, clean=True):
        self.clean = clean

    def to_record(self):
        return {"clean": self.clean}


def _build(
    monkeypatch,
    *,
    status="CONFIRMED",
    actions=None,
    fresh_plan_drift=False,
    target_drift=False,
):
    if status == "FAILED" and actions is None:
        actions = [
            MODULE.ACTION_INGEST_CHAIN,
            MODULE.ACTION_INGEST_RECEIPT,
            MODULE.ACTION_APPLY_EFFECT,
        ]
    saved = _plan(status=status, actions=actions)
    receipt = _receipt(status=status)
    snapshot = {"signature": SIGNATURE, "slot": 123}
    target = {
        "decision_id": DECISION_ID,
        "position": POSITION if status == "CONFIRMED" else None,
    }

    calls = []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plan_path = root / "plan.json"
        plan_path.write_text(json.dumps(saved), encoding="utf-8")
        receipt_path = root / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        database = root / "pio.db"
        database.write_bytes(b"db")
        binary = root / "executor"
        binary.write_bytes(b"binary")

        class FakeStorage:
            pass

        class FakePlanner:
            @staticmethod
            def _load_json(path, *, label):
                return json.loads(Path(path).read_text(encoding="utf-8"))

            @staticmethod
            def validate_reconciliation_plan(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_reconciliation_plan(**kwargs):
                fresh = copy.deepcopy(saved)
                if fresh_plan_drift:
                    fresh["plan_sha256"] = "b" * 64
                return fresh

            @staticmethod
            def _inspect_transaction(**kwargs):
                return copy.deepcopy(snapshot)

            @staticmethod
            def _validate_transaction_snapshot(snapshot_value, receipt_value):
                assert snapshot_value["signature"] == SIGNATURE

            @staticmethod
            def _sha256_value(value):
                return _sha(value)

            @staticmethod
            def _database_state(path):
                FakePlanner.db_calls += 1
                if FakePlanner.db_calls == 1:
                    return {"database": "1" * 64, "wal": None, "shm": None}
                return {"database": "2" * 64, "wal": None, "shm": None}

            @staticmethod
            def _load_reviewed_runtime(source):
                return object(), runtime

            @staticmethod
            def _target_state(*args, **kwargs):
                if target_drift:
                    return {"decision_id": DECISION_ID, "position": "different"}
                return copy.deepcopy(target)

        FakePlanner.db_calls = 0

        def ingest_chain(storage, snapshot_value, *, observed_at):
            calls.append(MODULE.ACTION_INGEST_CHAIN)
            return SimpleNamespace(signature=SIGNATURE)

        def ingest_receipt(storage, receipt_value, *, observed_at):
            calls.append(MODULE.ACTION_INGEST_RECEIPT)
            return SimpleNamespace(
                chain_snapshot_reconciled=True,
                reused_existing=False,
            )

        def apply_effect(storage, decision_id):
            calls.append(MODULE.ACTION_APPLY_EFFECT)
            return SimpleNamespace(
                effect=SimpleNamespace(
                    position_address=POSITION if status == "CONFIRMED" else None
                ),
                reused_existing=False,
            )

        def apply_position(storage, decision_id):
            calls.append(MODULE.ACTION_APPLY_POSITION)
            return SimpleNamespace(
                position_address=POSITION,
                next_status="OPEN",
                reused_existing=False,
            )

        runtime = {
            "Storage": FakeStorage,
            "ingest_transaction_events": ingest_chain,
            "ingest_execution_receipt": ingest_receipt,
            "apply_live_execution_effect": apply_effect,
            "apply_live_position_effect": apply_position,
            "audit_execution_receipts": lambda storage: _Audit(True),
            "audit_live_execution_ledger": lambda storage: _Audit(True),
        }

        monkeypatch.setattr(MODULE, "_load_planner", lambda source: FakePlanner)

        report = MODULE.apply_reconciliation(
            source_tree=ROOT,
            saved_plan_path=plan_path,
            expected_plan_sha256=saved["plan_sha256"],
            saved_execution_receipt_path=receipt_path,
            expected_execution_receipt_sha256="c" * 64,
            pio_database_path=database,
            executor_binary_path=binary,
            expected_executor_binary_sha256="d" * 64,
            rpc_url="https://rpc.example.invalid",
            observed_at=saved["observed_at"],
        )
        return report, calls


def _reseal(report):
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["apply_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_planner_blob_is_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_confirmed_reconciliation_applies_exact_planned_actions(monkeypatch):
    report, calls = _build(monkeypatch, status="CONFIRMED")

    assert calls == report["planned_actions"]
    assert report["applied_actions"] == report["planned_actions"]
    assert report["target_state_matches_plan"] is True
    assert report["reconciliation_apply_complete"] is True
    assert report["reconciliation_remaining"] is False
    assert report["requires_learning_label_reconciliation"] is True
    assert report["transaction_submission_authorized"] is False
    assert report["new_live_capital_authorized"] is False
    assert report["phase7_promotion_persisted"] is False


def test_failed_reconciliation_never_applies_position(monkeypatch):
    report, calls = _build(monkeypatch, status="FAILED")

    assert MODULE.ACTION_APPLY_POSITION not in calls
    assert report["requires_learning_label_reconciliation"] is False
    assert report["reconciliation_apply_complete"] is True


def test_fresh_plan_drift_blocks_before_any_mutation(monkeypatch):
    with pytest.raises(ValueError, match="fresh Phase 7 reconciliation plan differs"):
        _build(monkeypatch, fresh_plan_drift=True)


def test_target_state_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="production reconciliation target differs"):
        _build(monkeypatch, target_drift=True)


def test_existing_storage_bypasses_schema_initializer():
    class Storage:
        def __init__(self, path):
            raise AssertionError("initializer must not run")

    storage = MODULE._existing_storage(Storage, Path("/tmp/pio.db"))
    assert storage.path == Path("/tmp/pio.db")


def test_resealed_apply_cannot_authorize_submission(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="transaction_submission_authorized=false"):
        MODULE.validate_reconciliation_apply(report)


def test_resealed_apply_cannot_authorize_new_live_capital(monkeypatch):
    report, _ = _build(monkeypatch)
    report["new_live_capital_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="new_live_capital_authorized=false"):
        MODULE.validate_reconciliation_apply(report)


def test_resealed_apply_cannot_persist_phase7_promotion(monkeypatch):
    report, _ = _build(monkeypatch)
    report["phase7_promotion_persisted"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase7_promotion_persisted=false"):
        MODULE.validate_reconciliation_apply(report)


def test_apply_tool_has_no_transaction_execution_or_retry_path():
    source = TOOL.read_text(encoding="utf-8")

    assert '"controlled-live-submit-once"' not in source
    assert '"controlled-live-submit"' not in source
    assert '"execution-confirmation"' not in source
    assert '"execution-recovery"' not in source
    assert "begin_signing" not in source
    assert "send_transaction" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"automatic_resubmission_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
