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
    / "check_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_next_terminal_settlement_tick_post_audit.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_next_terminal_settlement_tick_post_audit",
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
    open_status: str = "OPEN",
    item_status: str = "APPLIED",
) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.executescript(
            """
            CREATE TABLE paper_runs(
                run_id TEXT PRIMARY KEY,
                observed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                items_total INTEGER NOT NULL,
                items_applied INTEGER NOT NULL,
                items_skipped INTEGER NOT NULL,
                items_failed INTEGER NOT NULL
            );
            CREATE TABLE paper_run_items(
                run_id TEXT NOT NULL,
                position_id TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error TEXT
            );
            CREATE TABLE paper_chain_valuations(
                position_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                active_bin_id INTEGER NOT NULL,
                mark_quote TEXT NOT NULL,
                fee_delta_quote TEXT NOT NULL,
                reward_delta_quote TEXT NOT NULL
            );
            CREATE TABLE paper_positions(
                position_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                status TEXT NOT NULL,
                policy_source TEXT NOT NULL,
                model_id TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                current_mark_quote TEXT NOT NULL,
                realized_pnl_quote TEXT
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
        failed = int(item_status == "FAILED")
        applied = int(item_status == "APPLIED")
        conn.execute(
            """
            INSERT INTO paper_runs
            VALUES (
                'settle-run', '2026-09-30T09:35:00+00:00',
                ?, 1, ?, 0, ?
            )
            """,
            ("FAILED" if failed else "COMPLETED", applied, failed),
        )
        conn.execute(
            """
            INSERT INTO paper_run_items
            VALUES (
                'settle-run', 'p8-pair-2-challenger', ?,
                ?, ?
            )
            """,
            (
                item_status,
                (
                    json.dumps({"executed_action": "HOLD"})
                    if item_status == "APPLIED"
                    else None
                ),
                "fixture failure" if item_status == "FAILED" else None,
            ),
        )
        if item_status == "APPLIED":
            conn.execute(
                """
                INSERT INTO paper_chain_valuations
                VALUES (
                    'p8-pair-2-challenger',
                    '2026-09-30T09:35:00+00:00',
                    'APPLIED', 100, '1000', '1', '0'
                )
                """
            )
        conn.execute(
            """
            INSERT INTO paper_positions
            VALUES (
                'p8-pair-2-incumbent', 'paper-1', 'pool-2',
                'CLOSED', 'ML_CHAMPION', 'champion-1',
                '2026-09-30T09:00:00+00:00',
                '2026-09-30T09:30:00+00:00',
                '0', '10'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_positions
            VALUES (
                'p8-pair-2-challenger', 'paper-1', 'pool-2',
                ?, 'ML_CHALLENGER', 'challenger-1',
                '2026-09-30T09:00:00+00:00',
                ?, ?, ?
            )
            """,
            (
                open_status,
                (
                    "2026-09-30T09:35:00+00:00"
                    if open_status == "CLOSED"
                    else None
                ),
                "0" if open_status == "CLOSED" else "1000",
                "5" if open_status == "CLOSED" else None,
            ),
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
            INSERT INTO continuous_learning_cycles
            VALUES (
                'cycle-1', 'PAPER_CHALLENGER', 'ACTIVE',
                'champion-1', 'challenger-1'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _receipt(
    production: Path,
    database: Path,
    *,
    open_status: str,
    item_status: str,
) -> dict:
    failed = int(item_status == "FAILED")
    applied = int(item_status == "APPLIED")
    return {
        "receipt_sha256": "a" * 64,
        "source_terminal_evaluation_sha256": "b" * 64,
        "source_terminal_post_audit_sha256": "c" * 64,
        "source_recursive_rollover_reentry_cycle_next_post_audit_sha256": "d" * 64,
        "recursive_rollover_reentry_cycle_next_entry_request_sha256": "1" * 64,
        "recursive_rollover_reentry_cycle_next_input_verification_sha256": "2" * 64,
        "source_final_evaluation_sha256": "e" * 64,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_after": _sha(database.read_bytes()),
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
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
        "open_position_id": "p8-pair-2-challenger",
        "closed_position_id": "p8-pair-2-incumbent",
        "open_position_policy_source": "ML_CHALLENGER",
        "open_position_model_id": "challenger-1",
        "settlement_cycle_id": "phase8-repeat-rollover-settle:pair-2:abc",
        "expected_run_id": "settle-run",
        "target_chain_observed_at": "2026-09-30T09:35:00+00:00",
        "items_total": 1,
        "items_applied": applied,
        "items_skipped": 0,
        "items_failed": failed,
        "run_item_status": item_status,
        "settlement_tick_complete": failed == 0,
        "settlement_tick_partial_failure": failed > 0,
    }


class _Ledger:
    passing = True
    reasons = ()

    def to_record(self):
        return {"passing": True, "reasons": []}


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    open_status: str = "OPEN",
    item_status: str = "APPLIED",
    state_override: dict | None = None,
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
        open_status=open_status,
        item_status=item_status,
    )
    receipt = _receipt(
        production,
        database,
        open_status=open_status,
        item_status=item_status,
    )
    if pair_regression:
        receipt["pair_id"] = receipt["previous_pair_id"]
    receipt_path = _write(root / "receipt.json", receipt)

    class SettlementReadiness:
        @staticmethod
        def _database_path(repo):
            return (Path(repo) / "data" / "pio.db").resolve()

        @staticmethod
        def _database_state(path):
            if state_override is not None:
                return copy.deepcopy(state_override)
            return {
                "database": _sha(Path(path).read_bytes()),
                "wal": None,
                "shm": None,
            }

    class Live:
        class Storage:
            def __init__(self, path):
                self.path = Path(path)

    class Readiness:
        @staticmethod
        def _load_reviewed(source):
            return SettlementReadiness, object(), object(), Live, object()

    class Executor:
        @staticmethod
        def validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_next_terminal_settlement_tick_execution_receipt(
            value,
        ):
            assert isinstance(value, dict)

        @staticmethod
        def _load_readiness(source):
            return Readiness

    class LedgerModule:
        @staticmethod
        def audit_paper_ledger(storage, *, account_id):
            assert account_id == "paper-1"
            return _Ledger()

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (Executor, LedgerModule),
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_next_terminal_settlement_tick_post_audit(
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
    report["post_audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_open_leg_routes_to_next_settlement_tick(monkeypatch):
    temp, run = _build(monkeypatch, open_status="OPEN")
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["receipt_valid"] is True
    assert report["previous_pair_id"] == "pair-1"
    assert report["pair_id"] == "pair-2"
    assert report["source_recursive_rollover_reentry_cycle_next_post_audit_sha256"] == "d" * 64
    assert report["recursive_rollover_reentry_cycle_next_entry_request_sha256"] == "1" * 64
    assert report["recursive_rollover_reentry_cycle_next_input_verification_sha256"] == "2" * 64
    assert report["source_final_evaluation_sha256"] == "e" * 64
    assert report["entry_observed_at"] == "2026-09-30T09:20:00+00:00"
    assert report["terminal_tick_observed_at"] == "2026-09-30T09:30:00+00:00"
    assert report["run_identity_verified"] is True
    assert report["run_scope_verified"] is True
    assert report["ledger_audit_passing"] is True
    assert report["closed_leg_stayed_closed"] is True
    assert report["open_leg_still_open"] is True
    assert report["open_leg_now_closed"] is False
    assert report["next_debt_type"] == "PAPER_PAIR_SETTLEMENT_REQUIRED"
    assert report["continuation_route"] == (
        "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_CYCLE_NEXT_TERMINAL_SETTLEMENT_NEXT_TICK_REVIEW"
    )
    assert report["next_settlement_tick_review_ready"] is True
    assert report["final_pair_evaluation_ready"] is False
    assert report["paper_settlement_tick_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_closed_remaining_leg_routes_to_final_evaluation(monkeypatch):
    temp, run = _build(monkeypatch, open_status="CLOSED")
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["open_leg_still_open"] is False
    assert report["open_leg_now_closed"] is True
    assert report["next_debt_type"] == (
        "PAPER_PAIR_FINAL_EVALUATION_REQUIRED"
    )
    assert report["continuation_route"] == (
        "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_CYCLE_NEXT_POST_SETTLEMENT_FINAL_EVALUATION_REVIEW"
    )
    assert report["final_pair_evaluation_ready"] is True
    assert report["next_settlement_tick_review_ready"] is False


def test_failed_settlement_tick_routes_to_recovery(monkeypatch):
    temp, run = _build(
        monkeypatch,
        open_status="OPEN",
        item_status="FAILED",
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["receipt_settlement_tick_partial_failure"] is True
    assert report["settlement_tick_partial_failure_verified"] is True
    assert report["next_debt_type"] == (
        "PAPER_SETTLEMENT_TICK_RECOVERY_REQUIRED"
    )
    assert report["continuation_route"] == (
        "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_CYCLE_NEXT_TERMINAL_SETTLEMENT_RECOVERY_REVIEW"
    )
    assert report["settlement_recovery_review_ready"] is True


def test_cycle_next_pair_lineage_regression_fails_closed(monkeypatch):
    temp, run = _build(monkeypatch, pair_regression=True)
    try:
        with pytest.raises(ValueError, match="pair id was not advanced"):
            run()
    finally:
        temp.cleanup()


def test_database_drift_after_receipt_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        state_override={
            "database": "9" * 64,
            "wal": None,
            "shm": None,
        },
    )
    try:
        with pytest.raises(ValueError, match="changed after"):
            run()
    finally:
        temp.cleanup()


def test_resealed_audit_cannot_authorize_next_tick(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_settlement_tick_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_settlement_tick_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_next_terminal_settlement_tick_post_audit(
            report
        )


def test_resealed_audit_cannot_authorize_promotion(monkeypatch):
    temp, run = _build(monkeypatch, open_status="CLOSED")
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
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_next_terminal_settlement_tick_post_audit(
            report
        )


def test_post_audit_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_live_chain_paper_batch(" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_settlement_tick_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
