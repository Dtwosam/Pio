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
    / "apply_phase7_controlled_live_exit_learning_label.py"
)

SPEC = importlib.util.spec_from_file_location(
    "apply_phase7_controlled_live_exit_learning_label",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


POSITION = "33333333333333333333333333333333"
POOL = "11111111111111111111111111111111"
SETTLEMENT_DECISION_ID = "PHASE7_EXIT_SETTLEMENT:" + "c" * 64


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


class _Storage:
    pass


class _Record:
    def __init__(self, value):
        self.value = value

    def to_record(self):
        return dict(self.value)


class _FakePlanner:
    saved = None
    fresh_override = None
    before_digest = "b" * 64
    after_digest = "d" * 64
    target_digest = "3" * 64
    state_calls = 0

    @staticmethod
    def _load_json(path, *, label):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @staticmethod
    def validate_exit_learning_label_plan(value):
        assert isinstance(value, dict)

    @classmethod
    def build_exit_learning_label_plan(cls, **kwargs):
        value = dict(cls.saved)
        if cls.fresh_override:
            value.update(cls.fresh_override)
        return value

    @classmethod
    def _database_state(cls, database):
        cls.state_calls += 1
        return {
            "database": (
                cls.before_digest
                if cls.state_calls == 1
                else cls.after_digest
            ),
            "wal": None,
            "shm": None,
        }

    @staticmethod
    def _load_reviewed(source):
        runtime = {
            "Storage": _Storage,
            "value_live_position_outcome": (
                lambda storage, position_address, max_age_seconds: SimpleNamespace(
                    valuation=_Record(VALUATION),
                    reused_existing=False,
                )
            ),
            "build_live_learning_label": (
                lambda storage, position_address: SimpleNamespace(
                    label=_Record(LABEL),
                    reused_existing=False,
                )
            ),
        }
        return object(), runtime

    @staticmethod
    def _target_state(database, position_address):
        return {
            "outcome": {"label_status": "VALUED"},
            "valuation": dict(VALUATION),
            "learning_label": dict(LABEL),
        }

    @staticmethod
    def _canonical_bytes(value):
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")


def _saved(database: Path) -> dict:
    target = _FakePlanner._target_state(database, POSITION)
    target_sha = hashlib.sha256(
        _FakePlanner._canonical_bytes(target)
    ).hexdigest()
    return {
        "plan_sha256": "a" * 64,
        "learning_label_plan_ready": True,
        "learning_label_reconciliation_required": True,
        "requires_separate_learning_label_apply": True,
        "opened_decision_id": "11111111-2222-4333-8444-555555555555",
        "principal_exit_decision_id": (
            "01234567-89ab-4def-8123-456789abcdef"
        ),
        "settlement_decision_id": SETTLEMENT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "pio_database_path": str(database),
        "pio_database_sha256_before": "b" * 64,
        "pio_wal_sha256_before": None,
        "pio_shm_sha256_before": None,
        "max_quote_age_seconds": 300,
        "private_valuation": dict(VALUATION),
        "private_learning_label": dict(LABEL),
        "private_target_state_sha256": target_sha,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    fresh_override=None,
    before_digest="b" * 64,
    target_override=None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production")
        saved = _saved(database)
        plan_path = _write(root / "plan.json", saved)
        audit_path = _write(root / "audit.json", {"audit_sha256": "e" * 64})

        _FakePlanner.saved = saved
        _FakePlanner.fresh_override = fresh_override
        _FakePlanner.before_digest = before_digest
        _FakePlanner.after_digest = "d" * 64
        _FakePlanner.state_calls = 0

        monkeypatch.setattr(
            MODULE,
            "_load_plan",
            lambda source: _FakePlanner,
        )
        if target_override is not None:
            monkeypatch.setattr(
                _FakePlanner,
                "_target_state",
                staticmethod(lambda database, position_address: target_override),
            )

        report = MODULE.apply_exit_learning_label(
            source_tree=ROOT,
            saved_plan_path=plan_path,
            expected_plan_sha256=saved["plan_sha256"],
            saved_post_reconciliation_audit_path=audit_path,
            expected_post_reconciliation_audit_sha256="e" * 64,
            pio_database_path=database,
            max_quote_age_seconds=300,
        )
        return report


def _reseal(report):
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["apply_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_plan_dependency_is_exactly_pinned():
    path = ROOT / MODULE.PLAN_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.PLAN_TOOL
    ]


def test_learning_label_apply_matches_private_replay(monkeypatch):
    report = _build(monkeypatch)

    assert report["valuation"] == VALUATION
    assert report["learning_label"] == LABEL
    assert report["target_state_matches_plan"] is True
    assert report["learning_label_apply_complete"] is True
    assert report["learning_label_reconciliation_remaining"] is False
    assert report["phase7_evidence_status_recheck_required"] is True
    assert report["requires_separate_phase7_promotion_action"] is True
    assert report["production_pio_database_modified"] is True
    assert report["transaction_submission_authorized"] is False
    assert report["phase7_promotion_authorized"] is False


def test_fresh_plan_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="fresh .* plan differs"):
        _build(
            monkeypatch,
            fresh_override={"plan_sha256": "f" * 64},
        )


def test_database_drift_after_planning_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="Pio database changed"):
        _build(
            monkeypatch,
            before_digest="f" * 64,
        )


def test_target_state_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="target differs"):
        _build(
            monkeypatch,
            target_override={"different": True},
        )


def test_saved_plan_without_apply_is_rejected(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production")
        saved = _saved(database)
        saved["learning_label_reconciliation_required"] = False
        saved["requires_separate_learning_label_apply"] = False
        plan_path = _write(root / "plan.json", saved)

        monkeypatch.setattr(
            MODULE,
            "_load_plan",
            lambda source: _FakePlanner,
        )
        with pytest.raises(ValueError, match="requires no production apply"):
            MODULE.apply_exit_learning_label(
                source_tree=ROOT,
                saved_plan_path=plan_path,
                expected_plan_sha256=saved["plan_sha256"],
                saved_post_reconciliation_audit_path=plan_path,
                expected_post_reconciliation_audit_sha256="e" * 64,
                pio_database_path=database,
            )


def test_resealed_apply_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_learning_label_apply(report)


def test_resealed_apply_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_learning_label_apply(report)


def test_learning_label_apply_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "value_live_position_outcome" in source
    assert "build_live_learning_label" in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
