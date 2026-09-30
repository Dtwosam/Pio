from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase8_paired_ml_paper_repeat_post_settlement_final_evaluation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_repeat_post_settlement_final_evaluation",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _FakeAudit:
    DEBT_FINAL_EVALUATION = "PAPER_PAIR_FINAL_EVALUATION_REQUIRED"
    ROUTE_FINAL_EVALUATION = (
        "PHASE8_PAIRED_ML_PAPER_REPEAT_POST_SETTLEMENT_FINAL_EVALUATION_REVIEW"
    )

    @staticmethod
    def validate_phase8_paired_ml_paper_repeat_terminal_settlement_tick_post_audit(
        value,
    ):
        assert isinstance(value, dict)


class _Criteria:
    min_challenger_closed_trades = 20
    min_incumbent_closed_trades = 20

    def to_record(self):
        return {
            "min_challenger_closed_trades": 20,
            "min_incumbent_closed_trades": 20,
        }


class _Storage:
    def __init__(self, path):
        self.path = Path(path)


class _Performance:
    def __init__(self, closed_trades: int):
        self.closed_trades = closed_trades

    def to_record(self):
        return {"closed_trades": self.closed_trades}


class _Validation:
    def __init__(
        self,
        *,
        incumbent_closed: int,
        challenger_closed: int,
        qualified: bool,
        reasons: tuple[str, ...],
    ):
        self.incumbent_model_id = "champion-1"
        self.challenger_model_id = "challenger-1"
        self.incumbent = _Performance(incumbent_closed)
        self.challenger = _Performance(challenger_closed)
        self.qualified = qualified
        self.policy_actionable = False
        self.reasons = reasons

    def to_record(self):
        return {
            "incumbent_model_id": self.incumbent_model_id,
            "challenger_model_id": self.challenger_model_id,
            "incumbent": self.incumbent.to_record(),
            "challenger": self.challenger.to_record(),
            "qualified": self.qualified,
            "policy_actionable": self.policy_actionable,
            "reasons": list(self.reasons),
        }


def _promotion(
    *,
    incumbent_closed: int,
    challenger_closed: int,
    qualified: bool,
    reasons: tuple[str, ...],
):
    class FakePromotion:
        ContinuousChampionCriteria = _Criteria
        Storage = _Storage

        @staticmethod
        def evaluate_continuous_champion(
            storage,
            *,
            cycle_id,
            account_id,
            criteria,
        ):
            assert cycle_id == "cycle-1"
            assert account_id == "paper-1"
            assert isinstance(criteria, _Criteria)
            return _Validation(
                incumbent_closed=incumbent_closed,
                challenger_closed=challenger_closed,
                qualified=qualified,
                reasons=reasons,
            )

    return FakePromotion


def _seed(
    database: Path,
    *,
    incumbent_status: str = "CLOSED",
    challenger_status: str = "CLOSED",
    challenger_policy: str = "ML_CHALLENGER",
    challenger_model: str = "challenger-1",
) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.execute(
            """
            CREATE TABLE paper_positions(
                position_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                status TEXT NOT NULL,
                policy_source TEXT NOT NULL,
                model_id TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                entry_capital_quote REAL NOT NULL,
                realized_pnl_quote REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_positions VALUES(
                'p8-pair-2-incumbent', 'paper-1', 'pool-2', ?,
                'ML_CHAMPION', 'champion-1',
                '2026-09-30T09:20:00+00:00',
                '2026-09-30T09:30:00+00:00',
                1000.0, 10.0
            )
            """,
            (incumbent_status,),
        )
        conn.execute(
            """
            INSERT INTO paper_positions VALUES(
                'p8-pair-2-challenger', 'paper-1', 'pool-2', ?,
                ?, ?,
                '2026-09-30T09:20:00+00:00',
                '2026-09-30T09:35:00+00:00',
                1000.0, 12.0
            )
            """,
            (challenger_status, challenger_policy, challenger_model),
        )
        conn.commit()
    finally:
        conn.close()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _audit(database: Path) -> dict:
    return {
        "post_audit_sha256": "a" * 64,
        "source_terminal_evaluation_sha256": "b" * 64,
        "source_terminal_post_audit_sha256": "c" * 64,
        "source_repeat_post_audit_sha256": "d" * 64,
        "source_final_evaluation_sha256": "e" * 64,
        "pio_database_path": str(database),
        "audit_database_sha256_after": _sha(database),
        "audit_wal_sha256_after": None,
        "audit_shm_sha256_after": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-1",
        "pair_id": "pair-2",
        "pool_address": "pool-2",
        "entry_observed_at": "2026-09-30T09:20:00+00:00",
        "terminal_tick_observed_at": "2026-09-30T09:30:00+00:00",
        "incumbent_position_id": "p8-pair-2-incumbent",
        "challenger_position_id": "p8-pair-2-challenger",
        "target_chain_observed_at": "2026-09-30T09:35:00+00:00",
        "final_pair_evaluation_ready": True,
        "open_leg_now_closed": True,
        "open_leg_still_open": False,
        "receipt_settlement_tick_partial_failure": False,
        "next_debt_type": "PAPER_PAIR_FINAL_EVALUATION_REQUIRED",
        "continuation_route": (
            "PHASE8_PAIRED_ML_PAPER_REPEAT_POST_SETTLEMENT_FINAL_EVALUATION_REVIEW"
        ),
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    incumbent_closed: int,
    challenger_closed: int,
    qualified: bool,
    reasons: tuple[str, ...] = (),
    incumbent_status: str = "CLOSED",
    challenger_status: str = "CLOSED",
    challenger_policy: str = "ML_CHALLENGER",
    challenger_model: str = "challenger-1",
    pair_regression: bool = False,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed(
        database,
        incumbent_status=incumbent_status,
        challenger_status=challenger_status,
        challenger_policy=challenger_policy,
        challenger_model=challenger_model,
    )
    audit = _audit(database)
    if pair_regression:
        audit["pair_id"] = audit["previous_pair_id"]
    audit_path = _write(root / "audit.json", audit)

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (
            _FakeAudit,
            _promotion(
                incumbent_closed=incumbent_closed,
                challenger_closed=challenger_closed,
                qualified=qualified,
                reasons=reasons,
            ),
        ),
    )

    def run():
        return (
            MODULE.build_phase8_paired_ml_paper_repeat_post_settlement_final_evaluation(
                repository=production,
                source_tree=ROOT,
                settlement_post_audit_path=audit_path,
            )
        )

    return temp, run


def _reseal(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["evaluation_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_fully_closed_pair_below_floor_routes_to_next_pair(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=7,
        challenger_closed=7,
        qualified=False,
        reasons=("closed trade floor not met",),
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["pair_both_closed"] is True
    assert report["previous_pair_id"] == "pair-1"
    assert report["pair_id"] == "pair-2"
    assert report["source_terminal_evaluation_sha256"] == "b" * 64
    assert report["source_repeat_post_audit_sha256"] == "d" * 64
    assert report["source_prior_final_evaluation_sha256"] == "e" * 64
    assert report["entry_observed_at"] == "2026-09-30T09:20:00+00:00"
    assert report["terminal_tick_observed_at"] == "2026-09-30T09:30:00+00:00"
    assert report["closed_trade_floor_met"] is False
    assert report["incumbent_closed_trades_remaining"] == 13
    assert report["challenger_closed_trades_remaining"] == 13
    assert report["next_debt_type"] == (
        "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
    )
    assert report["continuation_route"] == (
        "PHASE8_PAIRED_ML_PAPER_REPEAT_NEXT_PAIR_REVIEW"
    )
    assert report["next_pair_review_ready"] is True
    assert report["new_pair_entry_authorized"] is False
    assert report["continuous_promotion_authorized"] is False


def test_qualified_20_vs_20_routes_only_to_promotion_review(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=20,
        challenger_closed=20,
        qualified=True,
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["closed_trade_floor_met"] is True
    assert report["continuous_validation_qualified"] is True
    assert report["next_debt_type"] == (
        "PAPER_CHALLENGER_PROMOTION_REVIEW_REQUIRED"
    )
    assert report["continuation_route"] == (
        "PHASE8_CONTINUOUS_CHALLENGER_PROMOTION_REVIEW"
    )
    assert report["promotion_review_ready"] is True
    assert report["continuous_promotion_authorized"] is False
    assert report["phase8_promotion_authorized"] is False


def test_complete_failed_validation_routes_to_validation_review(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=22,
        challenger_closed=20,
        qualified=False,
        reasons=("challenger win rate is below required minimum",),
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["closed_trade_floor_met"] is True
    assert report["continuous_validation_qualified"] is False
    assert report["next_debt_type"] == (
        "PAPER_CHALLENGER_VALIDATION_REVIEW_REQUIRED"
    )
    assert report["continuation_route"] == (
        "PHASE8_CONTINUOUS_CHALLENGER_VALIDATION_REVIEW"
    )
    assert report["validation_review_ready"] is True
    assert report["promotion_review_ready"] is False


def test_refuses_repeat_pair_id_regression(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=5,
        challenger_closed=5,
        qualified=False,
        pair_regression=True,
    )
    try:
        with pytest.raises(ValueError, match="pair id was not advanced"):
            run()
    finally:
        temp.cleanup()


def test_refuses_if_either_pair_leg_is_not_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=5,
        challenger_closed=5,
        qualified=False,
        challenger_status="OPEN",
    )
    try:
        with pytest.raises(ValueError, match="both positions CLOSED"):
            run()
    finally:
        temp.cleanup()


def test_refuses_policy_or_model_binding_drift(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=20,
        challenger_closed=20,
        qualified=True,
        challenger_policy="ML_CHAMPION",
    )
    try:
        with pytest.raises(ValueError, match="policy/model binding changed"):
            run()
    finally:
        temp.cleanup()


def test_resealed_evaluation_cannot_authorize_new_pair(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=3,
        challenger_closed=3,
        qualified=False,
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    report["new_pair_entry_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="new_pair_entry_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_repeat_post_settlement_final_evaluation(
            report
        )


def test_resealed_evaluation_cannot_authorize_promotion(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=20,
        challenger_closed=20,
        qualified=True,
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    report["continuous_promotion_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="continuous_promotion_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_repeat_post_settlement_final_evaluation(
            report
        )


def test_final_evaluation_has_no_paper_or_promotion_mutation():
    source = TOOL.read_text(encoding="utf-8")

    assert "promote_continuous_challenger(" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "open_paired_ml_paper_entries(" not in source
    assert "UPDATE model_registry" not in source
    assert "INSERT INTO model_promotion_evidence" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"new_pair_entry_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
