from __future__ import annotations

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
    / "apply_phase7_controlled_live_exit_learning_label_v2.py"
)

SPEC = importlib.util.spec_from_file_location(
    "apply_phase7_controlled_live_exit_learning_label_v2",
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
ACTION = "BUILD_LIVE_LEARNING_LABEL"


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


class _FakeStorage:
    pass


class _FakeLabel:
    def to_record(self):
        return dict(LABEL)


class _FakePlanner:
    ACTION_BUILD_LABEL = ACTION
    saved = None
    fresh_override = None
    db_calls = 0

    @staticmethod
    def validate_exit_learning_label_plan_v2(value):
        assert isinstance(value, dict)

    @classmethod
    def build_exit_learning_label_plan_v2(cls, **kwargs):
        result = dict(cls.saved)
        if cls.fresh_override:
            result.update(cls.fresh_override)
        return result

    @staticmethod
    def _load_reviewed(source):
        runtime = {
            "Storage": _FakeStorage,
            "build_live_learning_label": (
                lambda storage, **kwargs: SimpleNamespace(
                    label=_FakeLabel(),
                    reused_existing=False,
                )
            ),
        }
        return object(), runtime

    @classmethod
    def _database_state(cls, path):
        cls.db_calls += 1
        if cls.db_calls == 1:
            return {"database": "b" * 64, "wal": None, "shm": None}
        return {"database": "c" * 64, "wal": None, "shm": None}

    @staticmethod
    def _label_state(database, *, position_address):
        return {
            "status": "CLOSED",
            "opened_decision_id": OPENED,
            "closed_decision_id": SETTLEMENT,
            "valuation_present": True,
            "learning_label": dict(LABEL),
        }

    @staticmethod
    def _sha256_bytes(payload):
        return "d" * 64

    @staticmethod
    def _canonical_bytes(value):
        return json.dumps(value, sort_keys=True).encode("utf-8")


def _plan(database: Path) -> dict:
    return {
        "plan_sha256": "a" * 64,
        "learning_label_plan_ready": True,
        "requires_separate_learning_label_apply": True,
        "planned_actions": [ACTION],
        "pre_learning_label_present": False,
        "new_live_entry_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_pio_database_modified": False,
        "opened_decision_id": OPENED,
        "principal_exit_decision_id": EXIT,
        "settlement_decision_id": SETTLEMENT,
        "pool_address": POOL,
        "position_address": POSITION,
        "pio_database_path": str(database),
        "pio_database_sha256_before": "b" * 64,
        "pio_wal_sha256_before": None,
        "pio_shm_sha256_before": None,
        "private_learning_label": dict(LABEL),
        "private_label_target_sha256": "d" * 64,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, *, fresh_override=None, before_sha="b" * 64):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production")
        plan = _plan(database)
        plan_path = _write(root / "plan.json", plan)
        audit_path = _write(root / "audit.json", {"audit_sha256": "e" * 64})

        _FakePlanner.saved = plan
        _FakePlanner.fresh_override = fresh_override
        _FakePlanner.db_calls = 0

        if before_sha != "b" * 64:
            @classmethod
            def state(cls, path):
                cls.db_calls += 1
                if cls.db_calls == 1:
                    return {
                        "database": before_sha,
                        "wal": None,
                        "shm": None,
                    }
                return {"database": "c" * 64, "wal": None, "shm": None}
            monkeypatch.setattr(_FakePlanner, "_database_state", state)

        monkeypatch.setattr(MODULE, "_load_planner", lambda source: _FakePlanner)

        return MODULE.apply_exit_learning_label_v2(
            source_tree=ROOT,
            saved_plan_path=plan_path,
            expected_plan_sha256=plan["plan_sha256"],
            saved_post_valuation_audit_path=audit_path,
            expected_post_valuation_audit_sha256="e" * 64,
            pio_database_path=database,
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["apply_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_planner_dependency_is_exactly_pinned():
    path = ROOT / MODULE.PLANNER_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.PLANNER_TOOL
    ]


def test_apply_persists_exact_new_learning_label(monkeypatch):
    report = _build(monkeypatch)

    assert report["planned_actions"] == [ACTION]
    assert report["applied_actions"] == [ACTION]
    assert report["learning_label"] == LABEL
    assert report["learning_label_reused_existing"] is False
    assert report["target_state_matches_plan"] is True
    assert report["learning_label_apply_complete"] is True
    assert report["learning_label_remaining"] is False
    assert report["production_pio_database_modified"] is True
    assert report["transaction_submission_authorized"] is False
    assert report["phase7_promotion_authorized"] is False


def test_fresh_plan_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="fresh .* plan differs",
    ):
        _build(
            monkeypatch,
            fresh_override={"plan_sha256": "f" * 64},
        )


def test_database_drift_after_planning_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="Pio database changed"):
        _build(monkeypatch, before_sha="f" * 64)


def test_resealed_apply_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_learning_label_apply_v2(report)


def test_resealed_apply_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_learning_label_apply_v2(report)


def test_learning_label_apply_has_no_transaction_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "build_live_learning_label" in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
