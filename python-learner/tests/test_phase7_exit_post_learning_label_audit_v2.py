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
    / "check_phase7_controlled_live_exit_post_learning_label_v2.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_post_learning_label_v2",
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


LABEL = {
    "position_address": POSITION,
    "decision_id": OPENED,
    "pool_address": POOL,
    "model_version": "m1",
    "strategy": "s1",
    "min_bin_id": 1,
    "max_bin_id": 3,
    "range_width_bins": 3,
    "proposed_capital_quote": "100",
    "expected_net_return_pct": "1.2",
    "expected_downside_pct": "0.5",
    "realized_pnl_quote": "4",
    "realized_return_bps": 400,
    "prediction_error_bps": 280,
    "target_positive_return": 1,
    "quote_unit": "ACCOUNT_QUOTE",
    "opened_signature": "enter-sig",
    "closed_decision_id": SETTLEMENT,
}


class _FakeApplyModule:
    @staticmethod
    def validate_exit_learning_label_apply_v2(value):
        assert isinstance(value, dict)


class _FakePlanner:
    db_sha = "b" * 64

    @classmethod
    def _database_state(cls, path):
        return {"database": cls.db_sha, "wal": None, "shm": None}

    @staticmethod
    def _label_state(database, *, position_address):
        return {
            "status": "CLOSED",
            "opened_decision_id": OPENED,
            "closed_decision_id": SETTLEMENT,
            "valuation_present": True,
            "learning_label": dict(LABEL),
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
        "learning_label_apply_complete": True,
        "learning_label_remaining": False,
        "requires_post_label_audit": True,
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
        "learning_label": dict(LABEL),
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    db_sha: str = "b" * 64,
    state_override: dict | None = None,
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
            "status": "CLOSED",
            "opened_decision_id": OPENED,
            "closed_decision_id": SETTLEMENT,
            "valuation_present": True,
            "learning_label": dict(LABEL),
        }
        if state_override:
            state.update(state_override)
        monkeypatch.setattr(
            _FakePlanner,
            "_label_state",
            staticmethod(lambda database, *, position_address: state),
        )

        return MODULE.build_exit_post_learning_label_audit_v2(
            source_tree=ROOT,
            saved_learning_label_apply_path=applied_path,
            expected_learning_label_apply_sha256=applied["apply_sha256"],
            pio_database_path=database,
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependency_is_exactly_pinned():
    path = ROOT / MODULE.LABEL_APPLY_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.LABEL_APPLY_TOOL
    ]


def test_post_label_audit_opens_phase7_status_recheck_only(monkeypatch):
    report = _build(monkeypatch)

    assert report["position_status"] == "CLOSED"
    assert report["valuation_present"] is True
    assert report["learning_label_present"] is True
    assert report["persisted_learning_label_matches_apply"] is True
    assert report["learning_label_reconciliation_complete"] is True
    assert report["phase7_evidence_status_recheck_ready"] is True
    assert report["post_label_audit_ready"] is True
    assert report["new_live_entry_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["phase7_promotion_authorized"] is False
    assert report["production_pio_database_modified"] is False


def test_database_drift_after_label_apply_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="changed after learning-label v2 apply",
    ):
        _build(monkeypatch, db_sha="f" * 64)


def test_persisted_label_must_match_apply(monkeypatch):
    bad = dict(LABEL)
    bad["prediction_error_bps"] = 999

    with pytest.raises(
        ValueError,
        match="persisted learning label differs",
    ):
        _build(
            monkeypatch,
            state_override={"learning_label": bad},
        )


def test_closed_position_is_required(monkeypatch):
    with pytest.raises(
        ValueError,
        match="requires CLOSED position",
    ):
        _build(
            monkeypatch,
            state_override={"status": "OPEN"},
        )


def test_resealed_audit_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_post_learning_label_audit_v2(report)


def test_resealed_audit_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_post_learning_label_audit_v2(report)


def test_post_label_audit_is_read_only():
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
