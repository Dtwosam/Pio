from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase7_controlled_live_state_reconciliation_plan.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_state_reconciliation_plan",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


RPC_URL = "https://rpc.example.invalid"
DECISION_ID = "11111111-2222-4333-8444-555555555555"
SIGNATURE = "sig-1"
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"


def _receipt_artifact(*, binary_sha: str, status: str) -> dict:
    confirmed = status == "CONFIRMED"
    return {
        "receipt_artifact_sha256": "a" * 64,
        "receipt_ready": True,
        "automatic_resubmission_performed": False,
        "phase7_promotion_persisted": False,
        "decision_id": DECISION_ID,
        "signature": SIGNATURE,
        "pool_address": POOL,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "executor_binary_sha256": binary_sha,
        "receipt": {
            "decision_id": DECISION_ID,
            "signature": SIGNATURE,
            "mode": "LIVE",
            "action": "ENTER",
            "pool_address": POOL,
            "intent_status": status,
            "slot": 123,
            "block_time": 456,
            "network_fee_lamports": 5000,
            "compute_units_consumed": 100000,
            "succeeded": confirmed,
            "event_count": 1 if confirmed else 0,
            "add_request_count": 1 if confirmed else 0,
            "rebalance_request_count": 0,
        },
    }


def _snapshot(status: str) -> dict:
    confirmed = status == "CONFIRMED"
    return {
        "signature": SIGNATURE,
        "slot": 123,
        "block_time": 456,
        "network_fee_lamports": 5000,
        "compute_units_consumed": 100000,
        "succeeded": confirmed,
        "add_requests": [{}] if confirmed else [],
        "rebalance_requests": [],
        "events": [{}] if confirmed else [],
    }


class _Audit:
    def __init__(self, *, clean: bool):
        self.clean = clean

    def to_record(self):
        return {"clean": self.clean}


def _build(
    monkeypatch,
    *,
    status: str = "CONFIRMED",
    pre: dict[str, bool] | None = None,
    receipt_clean: bool = True,
    ledger_clean: bool = True,
) -> dict:
    pre = copy.deepcopy(
        pre
        if pre is not None
        else {
            "chain": False,
            "receipt": False,
            "effect": False,
            "position_event": False,
        }
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        pio_db = root / "pio.db"
        pio_db.write_bytes(b"production-db")
        artifact = _receipt_artifact(binary_sha=binary_sha, status=status)
        artifact_path = root / "receipt.json"
        artifact_path.write_text(
            __import__("json").dumps(artifact),
            encoding="utf-8",
        )

        class FakeReceiptModule:
            @staticmethod
            def validate_receipt_artifact(value):
                assert isinstance(value, dict)

        class FakeStorage:
            def __init__(self, path):
                self.path = Path(path)

        def fake_ingest_transaction_events(storage, snapshot, *, observed_at):
            return SimpleNamespace(signature=SIGNATURE, events=len(snapshot["events"]))

        def fake_ingest_execution_receipt(storage, receipt, *, observed_at):
            return SimpleNamespace(
                decision_id=DECISION_ID,
                signature=SIGNATURE,
                reused_existing=pre["receipt"],
                chain_snapshot_reconciled=True,
            )

        def fake_apply_effect(storage, decision_id):
            return SimpleNamespace(
                effect=SimpleNamespace(
                    position_address=POSITION if status == "CONFIRMED" else None
                ),
                reused_existing=pre["effect"],
            )

        def fake_apply_position(storage, decision_id):
            return SimpleNamespace(
                decision_id=DECISION_ID,
                position_address=POSITION,
                next_status="OPEN",
            )

        runtime = {
            "Storage": FakeStorage,
            "ingest_transaction_events": fake_ingest_transaction_events,
            "ingest_execution_receipt": fake_ingest_execution_receipt,
            "apply_live_execution_effect": fake_apply_effect,
            "apply_live_position_effect": fake_apply_position,
            "audit_execution_receipts": lambda storage: _Audit(clean=receipt_clean),
            "audit_live_execution_ledger": lambda storage: _Audit(clean=ledger_clean),
        }

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_runtime",
            lambda source: (FakeReceiptModule, runtime),
        )
        monkeypatch.setattr(
            MODULE,
            "_inspect_transaction",
            lambda **kwargs: copy.deepcopy(_snapshot(status)),
        )
        monkeypatch.setattr(
            MODULE,
            "_backup_database",
            lambda source, destination: destination.write_bytes(b"private-db"),
        )
        monkeypatch.setattr(
            MODULE,
            "_pre_state",
            lambda *args, **kwargs: copy.deepcopy(pre),
        )
        monkeypatch.setattr(
            MODULE,
            "_target_state",
            lambda *args, **kwargs: {
                "decision_id": DECISION_ID,
                "status": status,
                "position": POSITION if status == "CONFIRMED" else None,
            },
        )
        stable = {
            "database": "1" * 64,
            "wal": None,
            "shm": None,
        }
        monkeypatch.setattr(
            MODULE,
            "_database_state",
            lambda path: copy.deepcopy(stable),
        )

        return MODULE.build_reconciliation_plan(
            source_tree=ROOT,
            saved_execution_receipt_path=artifact_path,
            expected_execution_receipt_sha256=artifact[
                "receipt_artifact_sha256"
            ],
            pio_database_path=pio_db,
            executor_binary_path=binary,
            expected_executor_binary_sha256=binary_sha,
            rpc_url=RPC_URL,
            observed_at="2026-09-29T09:00:00Z",
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["plan_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_confirmed_enter_plans_exact_missing_state(monkeypatch):
    report = _build(monkeypatch, status="CONFIRMED")

    assert report["planned_actions"] == [
        MODULE.ACTION_INGEST_CHAIN,
        MODULE.ACTION_INGEST_RECEIPT,
        MODULE.ACTION_APPLY_EFFECT,
        MODULE.ACTION_APPLY_POSITION,
    ]
    assert report["expected_position_address"] == POSITION
    assert report["reconciliation_required"] is True
    assert report["requires_separate_reconciliation_apply"] is True
    assert report["requires_post_apply_audit"] is True
    assert report["requires_position_state_reconciliation"] is True
    assert report["requires_learning_label_reconciliation"] is True
    assert report["production_pio_database_modified"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["new_live_capital_authorized"] is False


def test_failed_enter_never_plans_position_or_label(monkeypatch):
    report = _build(monkeypatch, status="FAILED")

    assert report["planned_actions"] == [
        MODULE.ACTION_INGEST_CHAIN,
        MODULE.ACTION_INGEST_RECEIPT,
        MODULE.ACTION_APPLY_EFFECT,
    ]
    assert report["expected_position_address"] is None
    assert report["requires_position_state_reconciliation"] is False
    assert report["requires_learning_label_reconciliation"] is False
    assert report["transaction_submission_authorized"] is False


def test_already_reconciled_state_requires_no_apply(monkeypatch):
    report = _build(
        monkeypatch,
        status="CONFIRMED",
        pre={
            "chain": True,
            "receipt": True,
            "effect": True,
            "position_event": True,
        },
    )

    assert report["planned_actions"] == []
    assert report["reconciliation_required"] is False
    assert report["requires_separate_reconciliation_apply"] is False
    assert report["requires_post_apply_audit"] is False
    assert report["requires_position_state_reconciliation"] is False
    assert report["requires_learning_label_reconciliation"] is True


def test_global_audit_debt_is_reported_without_blocking_exact_plan(monkeypatch):
    report = _build(
        monkeypatch,
        receipt_clean=False,
        ledger_clean=False,
    )

    assert report["reconciliation_plan_ready"] is True
    assert report["global_receipt_audit_clean_after_private_replay"] is False
    assert report["global_live_ledger_clean_after_private_replay"] is False


def test_transaction_snapshot_must_match_receipt_counts():
    receipt = _receipt_artifact(binary_sha="f" * 64, status="CONFIRMED")[
        "receipt"
    ]
    snapshot = _snapshot("CONFIRMED")
    snapshot["events"].append({})

    with pytest.raises(ValueError, match="event count differs"):
        MODULE._validate_transaction_snapshot(snapshot, receipt)


def test_observed_at_is_timezone_bound_and_normalized():
    assert (
        MODULE._normalize_observed_at("2026-09-29T10:00:00+01:00")
        == "2026-09-29T09:00:00+00:00"
    )

    with pytest.raises(ValueError, match="include a timezone"):
        MODULE._normalize_observed_at("2026-09-29T10:00:00")


def test_resealed_plan_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="transaction_submission_authorized=false"):
        MODULE.validate_reconciliation_plan(report)


def test_resealed_plan_cannot_authorize_new_live_capital(monkeypatch):
    report = _build(monkeypatch)
    report["new_live_capital_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="new_live_capital_authorized=false"):
        MODULE.validate_reconciliation_plan(report)


def test_resealed_plan_cannot_claim_production_db_mutation(monkeypatch):
    report = _build(monkeypatch)
    report["production_pio_database_modified"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="production_pio_database_modified=false"):
        MODULE.validate_reconciliation_plan(report)


def test_planner_is_private_replay_only():
    source = TOOL.read_text(encoding="utf-8")

    assert 'INSPECT_COMMAND = "inspect-transaction-events-env"' in source
    assert '"controlled-live-submit-once"' not in source
    assert '"controlled-live-submit"' not in source
    assert '"execution-confirmation"' not in source
    assert '"execution-recovery"' not in source
    assert "mode=ro" in source
    assert "TemporaryDirectory" in source
    assert '"production_pio_database_modified": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
