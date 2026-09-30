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
    / "check_phase8_paired_ml_paper_recursive_rollover_entry_post_audit.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_recursive_rollover_entry_post_audit",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _seed(
    database: Path,
    *,
    followup: bool = False,
    valuation: bool = False,
    challenger_policy: str = "ML_CHALLENGER",
) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.executescript(
            """
            CREATE TABLE paper_accounts(
                account_id TEXT PRIMARY KEY,
                cash_quote REAL NOT NULL
            );
            CREATE TABLE paper_positions(
                position_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                status TEXT NOT NULL,
                policy_source TEXT NOT NULL,
                model_id TEXT NOT NULL,
                strategy TEXT NOT NULL,
                min_bin_id INTEGER NOT NULL,
                max_bin_id INTEGER NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                entry_capital_quote REAL NOT NULL,
                entry_cost_quote REAL NOT NULL,
                current_mark_quote REAL NOT NULL,
                fee_income_quote REAL NOT NULL,
                reward_income_quote REAL NOT NULL,
                rebalance_cost_quote REAL NOT NULL,
                exit_cost_quote REAL NOT NULL,
                realized_pnl_quote REAL,
                rebalances INTEGER NOT NULL
            );
            CREATE TABLE paper_events(
                event_key TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                position_id TEXT,
                event_time TEXT NOT NULL,
                event_type TEXT NOT NULL,
                cash_delta_quote REAL NOT NULL,
                position_mark_quote REAL,
                fee_delta_quote REAL NOT NULL,
                reward_delta_quote REAL NOT NULL,
                cost_quote REAL NOT NULL,
                realized_pnl_quote REAL
            );
            CREATE TABLE paper_counterfactual_positions(
                position_id TEXT PRIMARY KEY,
                pool_address TEXT NOT NULL,
                entry_observed_at TEXT NOT NULL,
                amount_x_atomic INTEGER NOT NULL,
                amount_y_atomic INTEGER NOT NULL,
                capital_quote REAL NOT NULL,
                max_share_bps INTEGER NOT NULL,
                favor_x_active INTEGER NOT NULL,
                token_x_mint TEXT NOT NULL,
                token_y_mint TEXT NOT NULL
            );
            CREATE TABLE paper_chain_valuations(
                position_id TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );
            CREATE TABLE model_registry(
                model_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            );
            CREATE TABLE continuous_learning_cycles(
                cycle_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                active_key TEXT,
                champion_model_id TEXT NOT NULL,
                challenger_model_id TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO paper_accounts VALUES ('paper-1', 2990.0)"
        )
        positions = (
            (
                "p8-pair-4-incumbent",
                "ML_CHAMPION",
                "champion-1",
                "p8-pair-4:incumbent",
            ),
            (
                "p8-pair-4-challenger",
                challenger_policy,
                "challenger-1",
                "p8-pair-4:challenger",
            ),
        )
        for position_id, policy, model_id, event_key in positions:
            conn.execute(
                """
                INSERT INTO paper_positions VALUES(
                    ?, 'paper-1', 'pool-4', 'OPEN',
                    ?, ?, 'SPOT', 98, 102,
                    '2026-09-30T09:10:00+00:00', NULL,
                    1000.0, 5.0, 1000.0, 0.0, 0.0,
                    0.0, 0.0, NULL, 0
                )
                """,
                (position_id, policy, model_id),
            )
            conn.execute(
                """
                INSERT INTO paper_events VALUES(
                    ?, 'paper-1', ?,
                    '2026-09-30T09:10:00+00:00',
                    'ENTER', -1005.0, 1000.0,
                    0.0, 0.0, 5.0, NULL
                )
                """,
                (event_key, position_id),
            )
            conn.execute(
                """
                INSERT INTO paper_counterfactual_positions VALUES(
                    ?, 'pool-4', '2026-09-30T09:10:00+00:00',
                    10, 20, 1000.0, 500, 0, 'x-mint', 'y-mint'
                )
                """,
                (position_id,),
            )

        if followup:
            conn.execute(
                """
                INSERT INTO paper_events VALUES(
                    'later-mark', 'paper-1', 'p8-pair-4-challenger',
                    '2026-09-30T09:15:00+00:00',
                    'MARK', 0.0, 1001.0, 0.0, 0.0, 0.0, NULL
                )
                """
            )
        if valuation:
            conn.execute(
                """
                INSERT INTO paper_chain_valuations VALUES(
                    'p8-pair-4-challenger',
                    '2026-09-30T09:15:00+00:00'
                )
                """
            )

        conn.execute(
            "INSERT INTO model_registry VALUES ('champion-1', 'CHAMPION')"
        )
        conn.execute(
            """
            INSERT INTO model_registry
            VALUES ('challenger-1', 'PAPER_CHALLENGER')
            """
        )
        conn.execute(
            """
            INSERT INTO continuous_learning_cycles VALUES(
                'cycle-1', 'PAPER_CHALLENGER', 'ACTIVE',
                'champion-1', 'challenger-1'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _receipt(production: Path, database: Path) -> dict:
    return {
        "receipt_sha256": "a" * 64,
        "recursive_rollover_entry_request_sha256": "c" * 64,
        "recursive_rollover_entry_input_verification_sha256": "d" * 64,
        "source_final_evaluation_sha256": "b" * 64,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_after": _sha(database.read_bytes()),
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-3",
        "pair_id": "pair-4",
        "pool_address": "pool-4",
        "decision_observed_at": "2026-09-30T09:10:00+00:00",
        "incumbent_position_id": "p8-pair-4-incumbent",
        "challenger_position_id": "p8-pair-4-challenger",
        "incumbent_event_key": "p8-pair-4:incumbent",
        "challenger_event_key": "p8-pair-4:challenger",
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "required_pair_cash_quote": 2010.0,
        "account_cash_quote_after": 2990.0,
        "account_open_positions_after": 2,
        "incumbent_position": {
            "position_id": "p8-pair-4-incumbent",
        },
        "challenger_position": {
            "position_id": "p8-pair-4-challenger",
        },
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class _FakeStorage:
    def __init__(self, path):
        self.path = Path(path)


class _Ledger:
    def __init__(self, passing: bool):
        self.passing = passing
        self.reasons = () if passing else ("forced ledger mismatch",)

    def to_record(self):
        return {
            "passing": self.passing,
            "reasons": list(self.reasons),
        }


def _build(
    monkeypatch,
    *,
    followup: bool = False,
    valuation: bool = False,
    challenger_policy: str = "ML_CHALLENGER",
    ledger_passing: bool = True,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed(
        database,
        followup=followup,
        valuation=valuation,
        challenger_policy=challenger_policy,
    )
    receipt = _receipt(production, database)
    receipt_path = _write(root / "receipt.json", receipt)

    class FakeBaseAccount:
        @staticmethod
        def _production_database(repo):
            return (Path(repo) / "data" / "pio.db").resolve()

        @staticmethod
        def _database_state(path):
            path = Path(path)
            return {
                "database": _sha(path.read_bytes()),
                "wal": None,
                "shm": None,
            }

    class FakePair:
        Storage = _FakeStorage

    class FakeBaseReadiness:
        @staticmethod
        def _load_reviewed(source):
            return FakeBaseAccount, object(), object(), FakePair

    class FakeReadiness:
        @staticmethod
        def _load_reviewed(source):
            return (
                object(),
                object(),
                object(),
                object(),
                FakeBaseReadiness,
            )

    class FakeExecutor:
        @staticmethod
        def validate_phase8_paired_ml_paper_recursive_rollover_entry_execution_receipt(
            value,
        ):
            assert isinstance(value, dict)

        @staticmethod
        def _load_readiness(source):
            return FakeReadiness

    class FakeLedgerModule:
        @staticmethod
        def audit_paper_ledger(storage, *, account_id):
            assert account_id == "paper-1"
            return _Ledger(ledger_passing)

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (FakeExecutor, FakeLedgerModule),
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_recursive_rollover_entry_post_audit(
            repository=production,
            source_tree=ROOT,
            execution_receipt_path=receipt_path,
        )

    return temp, run


def _reseal(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["post_audit_sha256"] = _sha(
        MODULE._canonical_bytes(identity)
    )


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_recursive_rollover_post_audit_verifies_seed_and_ledger(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["previous_pair_id"] == "pair-3"
    assert report["pair_id"] == "pair-4"
    assert report["recursive_rollover_entry_request_sha256"] == "c" * 64
    assert report["recursive_rollover_entry_input_verification_sha256"] == "d" * 64
    assert report["fresh_account_open_positions"] == 2
    assert report["incumbent_position_verified"] is True
    assert report["challenger_position_verified"] is True
    assert report["enter_events_verified"] is True
    assert report["counterfactual_bindings_verified"] is True
    assert report["no_followup_paper_events_verified"] is True
    assert report["no_chain_valuations_applied_verified"] is True
    assert report["paper_ledger_audit_passing"] is True
    assert report["recursive_rollover_pair_seed_ready_for_supervision"] is True
    assert report["next_debt_type"] == "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
    assert report["continuation_route"] == (
        "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_SUPERVISION_REVIEW"
    )
    assert report["paper_supervisor_tick_authorized"] is False
    assert report["paper_evidence_collection_authorized"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_followup_event_before_audit_fails_closed(monkeypatch):
    temp, run = _build(monkeypatch, followup=True)
    try:
        with pytest.raises(ValueError, match="follow-up events"):
            run()
    finally:
        temp.cleanup()


def test_chain_valuation_before_audit_fails_closed(monkeypatch):
    temp, run = _build(monkeypatch, valuation=True)
    try:
        with pytest.raises(ValueError, match="chain valuations"):
            run()
    finally:
        temp.cleanup()


def test_ledger_failure_fails_closed(monkeypatch):
    temp, run = _build(monkeypatch, ledger_passing=False)
    try:
        with pytest.raises(ValueError, match="ledger audit failed"):
            run()
    finally:
        temp.cleanup()


def test_wrong_challenger_policy_binding_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        challenger_policy="ML_CHAMPION",
    )
    try:
        with pytest.raises(
            ValueError,
            match="ML_CHALLENGER position differs",
        ):
            run()
    finally:
        temp.cleanup()


def test_resealed_audit_cannot_authorize_supervisor_tick(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_supervisor_tick_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_supervisor_tick_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_entry_post_audit(
            report
        )


def test_resealed_audit_cannot_authorize_promotion(monkeypatch):
    temp, run = _build(monkeypatch)
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
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_entry_post_audit(
            report
        )


def test_resealed_audit_cannot_authorize_live_submit(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["live_submit_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="live_submit_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_entry_post_audit(
            report
        )


def test_recursive_rollover_post_audit_has_no_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "send_transaction" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
