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
    / "check_phase7_controlled_live_exit_post_valuation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_post_valuation",
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
    def validate_exit_position_valuation_apply(value):
        assert isinstance(value, dict)


class _FakePlanner:
    db_sha = "b" * 64

    @classmethod
    def _database_state(cls, path):
        return {"database": cls.db_sha, "wal": None, "shm": None}


VALUATION = {
    "position_address": POSITION,
    "pool_address": POOL,
    "opened_decision_id": OPENED,
    "closed_decision_id": SETTLEMENT,
    "quote_unit": "ACCOUNT_QUOTE",
    "valued_execution_count": 3,
    "principal_cashflow_quote": "100",
    "composition_cost_quote": "1",
    "fee_income_quote": "3",
    "reward_income_quote": "2",
    "network_cost_quote": "0.1",
    "realized_pnl_quote": "3.9",
    "entry_outflow_quote": "100",
    "realized_return_bps": 390,
    "max_age_seconds": 300,
    "quote_evidence": [],
}


def _apply(database: Path) -> dict:
    return {
        "apply_sha256": "a" * 64,
        "saved_plan_sha256": "1" * 64,
        "opened_decision_id": OPENED,
        "principal_exit_decision_id": EXIT,
        "settlement_decision_id": SETTLEMENT,
        "pool_address": POOL,
        "position_address": POSITION,
        "pio_database_path": str(database),
        "pio_database_sha256_after": "b" * 64,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
        "target_state_matches_plan": True,
        "valuation_apply_complete": True,
        "valuation_remaining": False,
        "requires_post_valuation_audit": True,
        "learning_label_reconciliation_required": True,
        "learning_label_reconciliation_ready": True,
        "phase7_evidence_status_recheck_required": True,
        "requires_separate_phase7_promotion_action": True,
        "production_pio_database_modified": True,
        "new_live_entry_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_repository_git_mutated": False,
        "valuation": dict(VALUATION),
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    state_override: dict | None = None,
    db_sha: str = "b" * 64,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production")
        applied = _apply(database)
        applied_path = _write(root / "apply.json", applied)

        _FakePlanner.db_sha = db_sha
        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeApplyModule, _FakePlanner),
        )
        state = {
            "outcome_label_status": "VALUED",
            "valuation": dict(VALUATION),
            "learning_label_present": False,
        }
        if state_override:
            state.update(state_override)
        monkeypatch.setattr(
            MODULE,
            "_state",
            lambda *args, **kwargs: state,
        )

        return MODULE.build_exit_post_valuation_audit(
            source_tree=ROOT,
            saved_valuation_apply_path=applied_path,
            expected_valuation_apply_sha256=applied["apply_sha256"],
            pio_database_path=database,
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependency_is_exactly_pinned():
    path = ROOT / MODULE.VALUATION_APPLY_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.VALUATION_APPLY_TOOL
    ]


def test_post_valuation_audit_opens_learning_label_only(monkeypatch):
    report = _build(monkeypatch)

    assert report["outcome_label_status"] == "VALUED"
    assert report["valuation_present"] is True
    assert report["persisted_valuation_matches_apply"] is True
    assert report["learning_label_present"] is False
    assert report["learning_label_reconciliation_required"] is True
    assert report["learning_label_reconciliation_ready"] is True
    assert report["post_valuation_audit_ready"] is True
    assert report["transaction_submission_authorized"] is False
    assert report["new_live_capital_authorized"] is False
    assert report["phase7_promotion_authorized"] is False


def test_database_drift_after_valuation_apply_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="changed after valuation apply"):
        _build(monkeypatch, db_sha="f" * 64)


def test_persisted_valuation_must_match_apply(monkeypatch):
    bad = dict(VALUATION)
    bad["realized_pnl_quote"] = "999"

    with pytest.raises(
        ValueError,
        match="persisted valuation differs",
    ):
        _build(
            monkeypatch,
            state_override={"valuation": bad},
        )


def test_premature_learning_label_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="learning label already exists",
    ):
        _build(
            monkeypatch,
            state_override={"learning_label_present": True},
        )


def test_resealed_audit_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_post_valuation_audit(report)


def test_resealed_audit_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_post_valuation_audit(report)


def test_post_valuation_audit_is_read_only():
    source = TOOL.read_text(encoding="utf-8")

    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "INSERT INTO" not in source
    assert "UPDATE live_" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
    assert '"production_pio_database_modified": False' in source
