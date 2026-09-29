from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase7_controlled_live_exit_learning_label_plan.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_learning_label_plan",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


POSITION = "33333333333333333333333333333333"
POOL = "11111111111111111111111111111111"
SETTLEMENT_DECISION_ID = "PHASE7_EXIT_SETTLEMENT:" + "c" * 64


class _FakeAuditModule:
    @staticmethod
    def validate_exit_final_post_reconciliation_audit(value):
        assert isinstance(value, dict)


class _FakeStorage:
    def __init__(self, path):
        self.path = Path(path)


class _Record:
    def __init__(self, value):
        self.value = value

    def to_record(self):
        return dict(self.value)


def _audit(database: Path) -> dict:
    return {
        "audit_sha256": "a" * 64,
        "opened_decision_id": "11111111-2222-4333-8444-555555555555",
        "principal_exit_decision_id": (
            "01234567-89ab-4def-8123-456789abcdef"
        ),
        "settlement_decision_id": SETTLEMENT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "pio_database_path": str(database),
        "pio_database_sha256_after": "b" * 64,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
        "target_state_matches_apply": True,
        "closure_proof_present": True,
        "position_outcome_present": True,
        "global_receipt_audit_clean": True,
        "global_live_ledger_clean": True,
        "exact_exit_lifecycle_reconciled": True,
        "learning_label_reconciliation_required": True,
        "phase7_evidence_status_recheck_required": True,
        "requires_separate_phase7_promotion_action": True,
        "position_status": "CLOSED",
        "open_position_lifecycle_followup_required": False,
        "production_pio_database_modified": False,
    }


VALUATION = {
    "position_address": POSITION,
    "pool_address": POOL,
    "opened_decision_id": "11111111-2222-4333-8444-555555555555",
    "closed_decision_id": SETTLEMENT_DECISION_ID,
    "quote_unit": "USD",
    "valued_execution_count": 3,
    "principal_cashflow_quote": "102",
    "composition_cost_quote": "1",
    "fee_income_quote": "2",
    "reward_income_quote": "1",
    "network_cost_quote": "1",
    "realized_pnl_quote": "3",
    "entry_outflow_quote": "100",
    "realized_return_bps": 300,
    "max_age_seconds": 300,
    "quote_evidence": (),
}


LABEL = {
    "position_address": POSITION,
    "decision_id": "11111111-2222-4333-8444-555555555555",
    "pool_address": POOL,
    "model_version": "model-v1",
    "strategy": "strategy",
    "min_bin_id": -10,
    "max_bin_id": 10,
    "range_width_bins": 21,
    "proposed_capital_quote": "100",
    "expected_net_return_pct": "1",
    "expected_downside_pct": "2",
    "realized_pnl_quote": "3",
    "realized_return_bps": 300,
    "prediction_error_bps": 200,
    "target_positive_return": 1,
    "quote_unit": "USD",
    "opened_signature": "open-sig",
    "closed_decision_id": SETTLEMENT_DECISION_ID,
}


def _runtime(
    *,
    valuation_reused=False,
    label_reused=False,
):
    return {
        "Storage": _FakeStorage,
        "value_live_position_outcome": (
            lambda storage, position_address, max_age_seconds: SimpleNamespace(
                valuation=_Record(VALUATION),
                reused_existing=valuation_reused,
            )
        ),
        "build_live_learning_label": (
            lambda storage, position_address: SimpleNamespace(
                label=_Record(LABEL),
                reused_existing=label_reused,
            )
        ),
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    pre=None,
    db_digest="b" * 64,
    runtime=None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production")
        audit = _audit(database)
        audit_path = _write(root / "audit.json", audit)

        runtime = runtime or _runtime()
        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeAuditModule, runtime),
        )
        monkeypatch.setattr(
            MODULE,
            "_database_state",
            lambda path: {
                "database": db_digest,
                "wal": None,
                "shm": None,
            },
        )
        monkeypatch.setattr(
            MODULE,
            "_pre_state",
            lambda database, position_address: (
                pre or {"valuation": False, "label": False}
            ),
        )
        monkeypatch.setattr(
            MODULE,
            "_snapshot_sqlite",
            lambda source, destination: shutil.copyfile(source, destination),
        )
        monkeypatch.setattr(
            MODULE,
            "_target_state",
            lambda database, position_address: {
                "outcome": {"label_status": "VALUED"},
                "valuation": dict(VALUATION),
                "learning_label": dict(LABEL),
            },
        )

        report = MODULE.build_exit_learning_label_plan(
            source_tree=ROOT,
            saved_post_reconciliation_audit_path=audit_path,
            expected_post_reconciliation_audit_sha256=audit["audit_sha256"],
            pio_database_path=database,
            max_quote_age_seconds=300,
        )
        return report


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


def test_private_valuation_and_label_plan_is_non_mutating(monkeypatch):
    report = _build(monkeypatch)

    assert report["private_valuation"]["position_address"] == POSITION
    assert report["private_valuation"]["realized_pnl_quote"] == "3"
    assert report["private_learning_label"]["position_address"] == POSITION
    assert report["private_learning_label"]["realized_return_bps"] == 300
    assert report["learning_label_reconciliation_required"] is True
    assert report["requires_separate_learning_label_apply"] is True
    assert report["requires_post_label_audit"] is True
    assert report["phase7_evidence_status_recheck_required"] is True
    assert report["transaction_submission_authorized"] is False
    assert report["phase7_promotion_authorized"] is False
    assert report["production_pio_database_modified"] is False


def test_already_valued_and_labeled_state_requires_no_apply(monkeypatch):
    report = _build(
        monkeypatch,
        pre={"valuation": True, "label": True},
        runtime=_runtime(
            valuation_reused=True,
            label_reused=True,
        ),
    )

    assert report["valuation_reused_existing"] is True
    assert report["learning_label_reused_existing"] is True
    assert report["learning_label_reconciliation_required"] is False
    assert report["requires_separate_learning_label_apply"] is False


def test_database_drift_after_post_reconciliation_audit_fails(monkeypatch):
    with pytest.raises(
        ValueError,
        match="changed after post-reconciliation audit",
    ):
        _build(
            monkeypatch,
            db_digest="f" * 64,
        )


def test_negative_quote_age_is_rejected(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production")
        audit = _audit(database)
        audit_path = _write(root / "audit.json", audit)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeAuditModule, _runtime()),
        )
        with pytest.raises(ValueError, match="non-negative integer"):
            MODULE.build_exit_learning_label_plan(
                source_tree=ROOT,
                saved_post_reconciliation_audit_path=audit_path,
                expected_post_reconciliation_audit_sha256=audit[
                    "audit_sha256"
                ],
                pio_database_path=database,
                max_quote_age_seconds=-1,
            )


def test_resealed_plan_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_learning_label_plan(report)


def test_resealed_plan_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_learning_label_plan(report)


def test_learning_label_plan_uses_private_snapshot_only():
    source = TOOL.read_text(encoding="utf-8")

    assert "mode=ro" in source
    assert "_snapshot_sqlite(database, private_db)" in source
    assert "value_live_position_outcome" in source
    assert "build_live_learning_label" in source
    assert "send_transaction" not in source
    assert "load_executor_keypair" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
    assert '"production_pio_database_modified": False' in source
