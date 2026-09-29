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
    / "apply_phase7_controlled_live_exit_position_valuation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "apply_phase7_controlled_live_exit_position_valuation",
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


class _FakeStorage:
    pass


class _FakeValuation:
    def to_record(self):
        return {
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


class _FakePlanner:
    ACTION_VALUE_POSITION = "VALUE_LIVE_POSITION_OUTCOME"
    saved = None
    fresh_override = None
    db_calls = 0

    @staticmethod
    def validate_exit_position_valuation_plan(value):
        assert isinstance(value, dict)

    @classmethod
    def build_exit_position_valuation_plan(cls, **kwargs):
        result = dict(cls.saved)
        if cls.fresh_override:
            result.update(cls.fresh_override)
        return result

    @staticmethod
    def _load_reviewed(source):
        runtime = {
            "Storage": _FakeStorage,
            "value_live_position_outcome": (
                lambda storage, **kwargs: SimpleNamespace(
                    valuation=_FakeValuation(),
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
    def _valuation_state(database, *, position_address):
        return {
            "outcome_label_status": "VALUED",
            "outcome": {
                "position_address": position_address,
                "label_status": "VALUED",
            },
            "valuation": {"position_address": position_address},
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
        "valuation_plan_ready": True,
        "valuation_required": True,
        "requires_separate_valuation_apply": True,
        "planned_actions": ["VALUE_LIVE_POSITION_OUTCOME"],
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
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
        "quote_unit": "ACCOUNT_QUOTE",
        "max_age_seconds": 300,
        "private_valuation_target_sha256": "d" * 64,
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

        original_state = _FakePlanner._database_state

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

        try:
            report = MODULE.apply_exit_position_valuation(
                source_tree=ROOT,
                saved_plan_path=plan_path,
                expected_plan_sha256=plan["plan_sha256"],
                saved_post_apply_audit_path=audit_path,
                expected_post_apply_audit_sha256="e" * 64,
                pio_database_path=database,
                max_age_seconds=300,
                quote_unit="ACCOUNT_QUOTE",
            )
        finally:
            if before_sha != "b" * 64:
                monkeypatch.setattr(
                    _FakePlanner,
                    "_database_state",
                    original_state,
                )
        return report


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


def test_apply_executes_exact_valuation_action(monkeypatch):
    report = _build(monkeypatch)

    assert report["planned_actions"] == ["VALUE_LIVE_POSITION_OUTCOME"]
    assert report["applied_actions"] == report["planned_actions"]
    assert report["post_outcome_label_status"] == "VALUED"
    assert report["target_state_matches_plan"] is True
    assert report["valuation_apply_complete"] is True
    assert report["valuation_remaining"] is False
    assert report["learning_label_reconciliation_ready"] is True
    assert report["production_pio_database_modified"] is True
    assert report["transaction_submission_authorized"] is False
    assert report["phase7_promotion_authorized"] is False


def test_fresh_plan_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="fresh valuation plan differs"):
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
        MODULE.validate_exit_position_valuation_apply(report)


def test_resealed_apply_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_position_valuation_apply(report)


def test_valuation_apply_has_no_transaction_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "value_live_position_outcome" in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
