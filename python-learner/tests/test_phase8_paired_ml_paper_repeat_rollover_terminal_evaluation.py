from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase8_paired_ml_paper_repeat_rollover_terminal_evaluation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_repeat_rollover_terminal_evaluation",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _audit(
    *,
    incumbent_status: str = "CLOSED",
    challenger_status: str = "CLOSED",
) -> dict:
    return {
        "post_audit_sha256": "a" * 64,
        "pio_database_path": "/opt/pio/data/pio.db",
        "audit_database_sha256_after": "b" * 64,
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
        "target_chain_observed_at": "2026-09-30T09:30:00+00:00",
        "source_rollover_post_audit_sha256": "c" * 64,
        "rollover_entry_request_sha256": "d" * 64,
        "rollover_entry_input_verification_sha256": "e" * 64,
        "source_final_evaluation_sha256": "f" * 64,
        "incumbent_position_id": "p8-pair-2-incumbent",
        "challenger_position_id": "p8-pair-2-challenger",
        "fresh_positions": {
            "p8-pair-2-incumbent": {
                "position_id": "p8-pair-2-incumbent",
                "status": incumbent_status,
            },
            "p8-pair-2-challenger": {
                "position_id": "p8-pair-2-challenger",
                "status": challenger_status,
            },
        },
        "terminal_pair_evaluation_ready": True,
        "tick_recovery_review_ready": False,
        "pair_any_closed": True,
        "continuation_route": (
            "PHASE8_PAIRED_ML_PAPER_REPEAT_ROLLOVER_TERMINAL_EVALUATION_REVIEW"
        ),
    }


class _FakeAudit:
    ROUTE_TERMINAL = (
        "PHASE8_PAIRED_ML_PAPER_REPEAT_ROLLOVER_TERMINAL_EVALUATION_REVIEW"
    )

    @staticmethod
    def validate_phase8_paired_ml_paper_repeat_rollover_continuation_evidence_tick_post_audit(
        value,
    ):
        assert isinstance(value, dict)

    @staticmethod
    def _load_reviewed(source):
        return _FakeExecutor, object()


class _FakeExecutor:
    @staticmethod
    def _load_readiness(source):
        return _FakeReadiness


class _FakeReadiness:
    @staticmethod
    def _load_reviewed(source):
        return _FakeSupervision, object(), object(), object(), object()


class _FakeSupervision:
    @staticmethod
    def _database_path(production):
        return (Path(production) / "data" / "pio.db").resolve()

    @staticmethod
    def _database_state(database):
        return {
            "database": "b" * 64,
            "wal": None,
            "shm": None,
        }


class _Criteria:
    min_challenger_closed_trades = 20
    min_incumbent_closed_trades = 20


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
        reasons: tuple[str, ...] = (),
    ):
        self.cycle_id = "cycle-1"
        self.account_id = "paper-1"
        self.incumbent_model_id = "champion-1"
        self.challenger_model_id = "challenger-1"
        self.incumbent = _Performance(incumbent_closed)
        self.challenger = _Performance(challenger_closed)
        self.qualified = qualified
        self.policy_actionable = False
        self.reasons = reasons

    def to_record(self):
        return {
            "cycle_id": self.cycle_id,
            "account_id": self.account_id,
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
    reasons: tuple[str, ...] = (),
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


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    incumbent_status: str = "CLOSED",
    challenger_status: str = "CLOSED",
    incumbent_closed: int = 1,
    challenger_closed: int = 1,
    qualified: bool = False,
    reasons: tuple[str, ...] = ("more evidence required",),
    state_after_override: dict | None = None,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"fixture")
    audit = _audit(
        incumbent_status=incumbent_status,
        challenger_status=challenger_status,
    )
    audit["pio_database_path"] = str(database)
    audit_path = _write(root / "audit.json", audit)

    class Supervision(_FakeSupervision):
        calls = 0

        @staticmethod
        def _database_state(database):
            Supervision.calls += 1
            if (
                state_after_override is not None
                and Supervision.calls > 1
            ):
                return copy.deepcopy(state_after_override)
            return {
                "database": "b" * 64,
                "wal": None,
                "shm": None,
            }

    class Readiness:
        @staticmethod
        def _load_reviewed(source):
            return Supervision, object(), object(), object(), object()

    class Executor:
        @staticmethod
        def _load_readiness(source):
            return Readiness

    class Audit(_FakeAudit):
        @staticmethod
        def _load_reviewed(source):
            return Executor, object()

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (
            Audit,
            _promotion(
                incumbent_closed=incumbent_closed,
                challenger_closed=challenger_closed,
                qualified=qualified,
                reasons=reasons,
            ),
        ),
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_repeat_rollover_terminal_evaluation(
            repository=production,
            source_tree=ROOT,
            terminal_post_audit_path=audit_path,
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


def test_one_closed_side_routes_to_pair_settlement(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_status="CLOSED",
        challenger_status="OPEN",
        incumbent_closed=7,
        challenger_closed=6,
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["previous_pair_id"] == "pair-1"
    assert report["pair_id"] == "pair-2"
    assert report["source_rollover_post_audit_sha256"] == "c" * 64
    assert report["rollover_entry_request_sha256"] == "d" * 64
    assert report["rollover_entry_input_verification_sha256"] == "e" * 64
    assert report["source_final_evaluation_sha256"] == "f" * 64
    assert report["pair_closed_count"] == 1
    assert report["pair_both_closed"] is False
    assert report["pair_has_open_remainder"] is True
    assert report["next_debt_type"] == "PAPER_PAIR_SETTLEMENT_REQUIRED"
    assert report["continuation_route"] == (
        "PHASE8_PAIRED_ML_PAPER_REPEAT_ROLLOVER_TERMINAL_SETTLEMENT_REVIEW"
    )
    assert report["pair_settlement_review_ready"] is True
    assert report["next_pair_review_ready"] is False
    assert report["promotion_review_ready"] is False
    assert report["continuous_promotion_authorized"] is False


def test_repeat_pair_id_cannot_regress(monkeypatch):
    audit = _audit()
    audit["pair_id"] = audit["previous_pair_id"]
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"fixture")
    audit["pio_database_path"] = str(database)
    audit_path = _write(root / "audit.json", audit)

    class Supervision(_FakeSupervision):
        pass

    class Readiness:
        @staticmethod
        def _load_reviewed(source):
            return Supervision, object(), object(), object(), object()

    class Executor:
        @staticmethod
        def _load_readiness(source):
            return Readiness

    class Audit(_FakeAudit):
        @staticmethod
        def _load_reviewed(source):
            return Executor, object()

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (
            Audit,
            _promotion(
                incumbent_closed=1,
                challenger_closed=1,
                qualified=False,
            ),
        ),
    )
    try:
        with pytest.raises(ValueError, match="pair id was not advanced"):
            MODULE.build_phase8_paired_ml_paper_repeat_rollover_terminal_evaluation(
                repository=production,
                source_tree=ROOT,
                terminal_post_audit_path=audit_path,
            )
    finally:
        temp.cleanup()


def test_closed_pair_below_20_routes_to_next_pair(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=12,
        challenger_closed=12,
        qualified=False,
        reasons=(
            "challenger closed_trades 12 < required 20",
            "incumbent closed_trades 12 < required 20",
        ),
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["pair_both_closed"] is True
    assert report["closed_trade_floor_met"] is False
    assert report["incumbent_closed_trades_remaining"] == 8
    assert report["challenger_closed_trades_remaining"] == 8
    assert report["next_debt_type"] == (
        "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
    )
    assert report["continuation_route"] == (
        "PHASE8_PAIRED_ML_PAPER_REPEAT_ROLLOVER_NEXT_PAIR_REVIEW"
    )
    assert report["next_pair_review_ready"] is True
    assert report["new_pair_entry_authorized"] is False


def test_qualified_20_vs_20_routes_only_to_promotion_review(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=20,
        challenger_closed=20,
        qualified=True,
        reasons=(),
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


def test_complete_but_failed_validation_routes_to_review(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=21,
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


def test_database_drift_during_evaluation_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        state_after_override={
            "database": "9" * 64,
            "wal": None,
            "shm": None,
        },
    )
    try:
        with pytest.raises(ValueError, match="changed production database"):
            run()
    finally:
        temp.cleanup()


def test_resealed_evaluation_cannot_authorize_promotion(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=20,
        challenger_closed=20,
        qualified=True,
        reasons=(),
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
        MODULE.validate_phase8_paired_ml_paper_repeat_rollover_terminal_evaluation(report)


def test_resealed_evaluation_cannot_authorize_new_pair(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_closed=5,
        challenger_closed=5,
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
        MODULE.validate_phase8_paired_ml_paper_repeat_rollover_terminal_evaluation(report)


def test_terminal_evaluation_has_no_paper_or_promotion_mutation():
    source = TOOL.read_text(encoding="utf-8")

    assert "promote_continuous_challenger(" not in source
    assert "open_paired_ml_paper_entries(" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "UPDATE model_registry" not in source
    assert "INSERT INTO model_promotion_evidence" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"new_pair_entry_authorized": False' in source
    assert '"live_submit_authorized": False' in source
