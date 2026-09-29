from __future__ import annotations

from dataclasses import dataclass
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
    / "build_phase7_controlled_live_exit_position_valuation_plan.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_position_valuation_plan",
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


class _FakeAuditModule:
    @staticmethod
    def validate_exit_final_post_apply_audit(value):
        assert isinstance(value, dict)


class _FakeStorage:
    def __init__(self, path):
        self.path = Path(path)


@dataclass
class _Valuation:
    position_address: str = POSITION
    pool_address: str = POOL
    opened_decision_id: str = OPENED
    closed_decision_id: str = SETTLEMENT
    quote_unit: str = "ACCOUNT_QUOTE"
    valued_execution_count: int = 3
    principal_cashflow_quote: str = "100"
    composition_cost_quote: str = "1"
    fee_income_quote: str = "3"
    reward_income_quote: str = "2"
    network_cost_quote: str = "0.1"
    realized_pnl_quote: str = "3.9"
    entry_outflow_quote: str = "100"
    realized_return_bps: int = 390
    max_age_seconds: int = 300
    quote_evidence: tuple = ()

    def to_record(self):
        return self.__dict__.copy()


def _audit(database: Path, *, label_status: str) -> dict:
    valued = label_status == "VALUED"
    return {
        "audit_sha256": "a" * 64,
        "opened_decision_id": OPENED,
        "principal_exit_decision_id": EXIT,
        "settlement_decision_id": SETTLEMENT,
        "pool_address": POOL,
        "position_address": POSITION,
        "pio_database_path": str(database),
        "pio_database_sha256_after": "b" * 64,
        "exact_exit_lifecycle_reconciled": True,
        "learning_label_reconciliation_required": True,
        "phase7_evidence_status_recheck_required": True,
        "post_apply_audit_ready": True,
        "requires_separate_phase7_promotion_action": True,
        "position_status": "CLOSED",
        "outcome_label_status": label_status,
        "live_position_valuation_required": not valued,
        "live_position_valuation_complete": valued,
        "learning_label_reconciliation_ready": valued,
        "new_live_entry_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_pio_database_modified": False,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    label_status: str = "ATOMIC_ONLY",
    db_sha: str = "b" * 64,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production-db")
        audit = _audit(database, label_status=label_status)
        audit_path = _write(root / "audit.json", audit)

        states = []
        if label_status == "ATOMIC_ONLY":
            states = [
                {
                    "outcome_label_status": "ATOMIC_ONLY",
                    "outcome": {
                        "position_address": POSITION,
                        "label_status": "ATOMIC_ONLY",
                    },
                    "valuation": None,
                },
                {
                    "outcome_label_status": "VALUED",
                    "outcome": {
                        "position_address": POSITION,
                        "label_status": "VALUED",
                    },
                    "valuation": {"position_address": POSITION},
                },
            ]
            reused = False
        else:
            states = [
                {
                    "outcome_label_status": "VALUED",
                    "outcome": {
                        "position_address": POSITION,
                        "label_status": "VALUED",
                    },
                    "valuation": {"position_address": POSITION},
                },
                {
                    "outcome_label_status": "VALUED",
                    "outcome": {
                        "position_address": POSITION,
                        "label_status": "VALUED",
                    },
                    "valuation": {"position_address": POSITION},
                },
            ]
            reused = True

        runtime = {
            "Storage": _FakeStorage,
            "DEFAULT_QUOTE_UNIT": "ACCOUNT_QUOTE",
            "value_live_position_outcome": (
                lambda storage, **kwargs: type(
                    "Result",
                    (),
                    {
                        "valuation": _Valuation(),
                        "reused_existing": reused,
                    },
                )()
            ),
        }

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeAuditModule, runtime),
        )
        monkeypatch.setattr(
            MODULE,
            "_database_state",
            lambda path: {"database": db_sha, "wal": None, "shm": None},
        )
        monkeypatch.setattr(
            MODULE,
            "_backup_database",
            lambda source, destination: shutil.copyfile(source, destination),
        )

        calls = {"count": 0}

        def fake_state(database_path, *, position_address):
            index = min(calls["count"], len(states) - 1)
            calls["count"] += 1
            return states[index]

        monkeypatch.setattr(MODULE, "_valuation_state", fake_state)

        report = MODULE.build_exit_position_valuation_plan(
            source_tree=ROOT,
            saved_post_apply_audit_path=audit_path,
            expected_post_apply_audit_sha256=audit["audit_sha256"],
            pio_database_path=database,
            max_age_seconds=300,
            quote_unit="ACCOUNT_QUOTE",
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


def test_atomic_only_outcome_plans_exactly_one_valuation(monkeypatch):
    report = _build(monkeypatch, label_status="ATOMIC_ONLY")

    assert report["planned_actions"] == [MODULE.ACTION_VALUE_POSITION]
    assert report["valuation_required"] is True
    assert report["pre_valuation_present"] is False
    assert report["valuation_reused_existing"] is False
    assert report["post_outcome_label_status"] == "VALUED"
    assert report["requires_separate_valuation_apply"] is True
    assert report["learning_label_reconciliation_ready"] is True
    assert report["production_pio_database_modified"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["phase7_promotion_authorized"] is False


def test_existing_valued_outcome_is_idempotent(monkeypatch):
    report = _build(monkeypatch, label_status="VALUED")

    assert report["planned_actions"] == []
    assert report["valuation_required"] is False
    assert report["pre_valuation_present"] is True
    assert report["valuation_reused_existing"] is True
    assert report["requires_separate_valuation_apply"] is False
    assert report["post_outcome_label_status"] == "VALUED"


def test_database_must_match_post_apply_audit(monkeypatch):
    with pytest.raises(
        ValueError,
        match="changed after post-apply audit",
    ):
        _build(
            monkeypatch,
            label_status="ATOMIC_ONLY",
            db_sha="f" * 64,
        )


def test_resealed_plan_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_position_valuation_plan(report)


def test_resealed_plan_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_position_valuation_plan(report)


def test_valuation_plan_is_private_replay_only():
    source = TOOL.read_text(encoding="utf-8")

    assert "mode=ro" in source
    assert "_backup_database(database, private_db)" in source
    assert "value_live_position_outcome" in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
    assert '"production_pio_database_modified": False' in source
