from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase8_recursive_reentry_checkpoint_v8_continuation_evidence_tick_post_audit.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_recursive_reentry_checkpoint_v8_continuation_evidence_tick_post_audit",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _seed_database(
    path: Path,
    *,
    partial: bool = False,
    closed_position: str | None = None,
    applied_valuation_missing: bool = False,
) -> None:
    conn = sqlite3.connect(path)
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
                event_key_prefix TEXT NOT NULL,
                status TEXT NOT NULL,
                active_bin_id INTEGER NOT NULL,
                mark_quote TEXT NOT NULL,
                fee_delta_quote TEXT NOT NULL,
                reward_delta_quote TEXT NOT NULL,
                inventory_x_atomic TEXT NOT NULL,
                inventory_y_atomic TEXT NOT NULL,
                fee_x_atomic TEXT NOT NULL,
                fee_y_atomic TEXT NOT NULL,
                reward_one_atomic TEXT NOT NULL,
                reward_two_atomic TEXT NOT NULL
            );
            CREATE TABLE paper_positions(
                position_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                status TEXT NOT NULL,
                policy_source TEXT NOT NULL,
                model_id TEXT,
                strategy TEXT NOT NULL,
                min_bin_id INTEGER NOT NULL,
                max_bin_id INTEGER NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                entry_capital_quote TEXT NOT NULL,
                entry_cost_quote TEXT NOT NULL,
                current_mark_quote TEXT NOT NULL,
                fee_income_quote TEXT NOT NULL,
                reward_income_quote TEXT NOT NULL,
                rebalance_cost_quote TEXT NOT NULL,
                exit_cost_quote TEXT NOT NULL,
                realized_pnl_quote TEXT,
                rebalances INTEGER NOT NULL
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
        run_status = "FAILED" if partial else "COMPLETE"
        applied = 1 if partial else 2
        failed = 1 if partial else 0
        conn.execute(
            """
            INSERT INTO paper_runs(
                run_id, observed_at, status, items_total,
                items_applied, items_skipped, items_failed
            ) VALUES (
                'expected-run',
                '2026-09-30T09:30:00+00:00',
                ?, 2, ?, 0, ?
            )
            """,
            (run_status, applied, failed),
        )

        statuses = {
            "p8-pair-2-incumbent": "APPLIED",
            "p8-pair-2-challenger": "FAILED" if partial else "APPLIED",
        }
        for position_id, status in statuses.items():
            conn.execute(
                """
                INSERT INTO paper_run_items(
                    run_id, position_id, status, result_json, error
                ) VALUES (
                    'expected-run', ?, ?, ?, ?
                )
                """,
                (
                    position_id,
                    status,
                    "{}" if status == "APPLIED" else None,
                    "forced failure" if status == "FAILED" else None,
                ),
            )
            position_status = (
                "CLOSED"
                if closed_position == position_id
                else "OPEN"
            )
            policy = (
                "ML_CHAMPION"
                if position_id == "p8-pair-2-incumbent"
                else "ML_CHALLENGER"
            )
            model_id = (
                "champion-1"
                if position_id == "p8-pair-2-incumbent"
                else "challenger-1"
            )
            conn.execute(
                """
                INSERT INTO paper_positions(
                    position_id, account_id, pool_address, status,
                    policy_source, model_id, strategy, min_bin_id, max_bin_id,
                    opened_at, closed_at, entry_capital_quote,
                    entry_cost_quote, current_mark_quote, fee_income_quote,
                    reward_income_quote, rebalance_cost_quote,
                    exit_cost_quote, realized_pnl_quote, rebalances
                ) VALUES (
                    ?, 'paper-1', 'pool-2', ?, ?, ?, 'SPOT', 98, 102,
                    '2026-09-30T09:20:00+00:00', ?,
                    '1000', '5', ?, '1', '0', '0', ?, ?, 0
                )
                """,
                (
                    position_id,
                    position_status,
                    policy,
                    model_id,
                    (
                        "2026-09-30T09:30:00+00:00"
                        if position_status == "CLOSED"
                        else None
                    ),
                    "0" if position_status == "CLOSED" else "1001",
                    "2" if position_status == "CLOSED" else "0",
                    "1" if position_status == "CLOSED" else None,
                ),
            )

        if not applied_valuation_missing:
            conn.execute(
                """
                INSERT INTO paper_chain_valuations(
                    position_id, observed_at, event_key_prefix, status,
                    active_bin_id, mark_quote, fee_delta_quote,
                    reward_delta_quote, inventory_x_atomic,
                    inventory_y_atomic, fee_x_atomic, fee_y_atomic,
                    reward_one_atomic, reward_two_atomic
                ) VALUES (
                    'p8-pair-2-incumbent',
                    '2026-09-30T09:30:00+00:00',
                    'paper-chain:p8-pair-2-incumbent:2026-09-30T09:30:00+00:00',
                    'APPLIED', 100, '1001', '1', '0',
                    '10', '20', '1', '0', '0', '0'
                )
                """
            )
        if partial:
            conn.execute(
                """
                INSERT INTO paper_chain_valuations(
                    position_id, observed_at, event_key_prefix, status,
                    active_bin_id, mark_quote, fee_delta_quote,
                    reward_delta_quote, inventory_x_atomic,
                    inventory_y_atomic, fee_x_atomic, fee_y_atomic,
                    reward_one_atomic, reward_two_atomic
                ) VALUES (
                    'p8-pair-2-challenger',
                    '2026-09-30T09:30:00+00:00',
                    'paper-chain:p8-pair-2-challenger:2026-09-30T09:30:00+00:00',
                    'PREPARED', 100, '999', '0', '0',
                    '10', '20', '0', '0', '0', '0'
                )
                """
            )
        else:
            conn.execute(
                """
                INSERT INTO paper_chain_valuations(
                    position_id, observed_at, event_key_prefix, status,
                    active_bin_id, mark_quote, fee_delta_quote,
                    reward_delta_quote, inventory_x_atomic,
                    inventory_y_atomic, fee_x_atomic, fee_y_atomic,
                    reward_one_atomic, reward_two_atomic
                ) VALUES (
                    'p8-pair-2-challenger',
                    '2026-09-30T09:30:00+00:00',
                    'paper-chain:p8-pair-2-challenger:2026-09-30T09:30:00+00:00',
                    'APPLIED', 100, '1002', '2', '0',
                    '10', '20', '2', '0', '0', '0'
                )
                """
            )

        conn.execute(
            "INSERT INTO model_registry(model_id, status) VALUES (?, ?)",
            ("champion-1", "CHAMPION"),
        )
        conn.execute(
            "INSERT INTO model_registry(model_id, status) VALUES (?, ?)",
            ("challenger-1", "PAPER_CHALLENGER"),
        )
        conn.execute(
            """
            INSERT INTO continuous_learning_cycles(
                cycle_id, status, active_key,
                champion_model_id, challenger_model_id
            ) VALUES (
                'cycle-1', 'PAPER_CHALLENGER', 'ACTIVE',
                'champion-1', 'challenger-1'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _receipt(production: Path, database: Path, *, partial: bool) -> dict:
    return {
        "receipt_sha256": "a" * 64,
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
        "source_checkpoint_sha256": "9" * 64,
        "source_post_audit_sha256": "2" * 64,
        "source_execution_receipt_sha256": "3" * 64,
        "source_pair_entry_post_audit_sha256": "4" * 64,
        "pair_entry_request_sha256": "5" * 64,
        "pair_entry_input_verification_sha256": "6" * 64,
        "pair_lineage_sha256": "7" * 64,
        "latest_previous_tick_post_audit_sha256": "8" * 64,
        "source_final_evaluation_sha256": "a" * 64,
        "previous_tick_observed_at": "2026-09-30T09:25:00+00:00",
        "requested_position_ids": [
            "p8-pair-2-incumbent",
            "p8-pair-2-challenger",
        ],
        "incumbent_position_id": "p8-pair-2-incumbent",
        "challenger_position_id": "p8-pair-2-challenger",
        "evidence_cycle_id": "phase8-recursive-reentry-checkpoint-continuation:pair-2:abc123",
        "expected_run_id": "expected-run",
        "target_chain_observed_at": "2026-09-30T09:30:00+00:00",
        "tick_status": "FAILED" if partial else "COMPLETE",
        "items_total": 2,
        "items_applied": 1 if partial else 2,
        "items_skipped": 0,
        "items_failed": 1 if partial else 0,
        "run_item_statuses": {
            "p8-pair-2-incumbent": "APPLIED",
            "p8-pair-2-challenger": "FAILED" if partial else "APPLIED",
        },
        "pair_tick_complete": not partial,
        "pair_tick_partial_failure": partial,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class _Ledger:
    def __init__(self, passing: bool):
        self.passing = passing
        self.reasons = () if passing else ("ledger drift",)

    def to_record(self):
        return {
            "account_id": "paper-1",
            "passing": self.passing,
            "reasons": list(self.reasons),
        }


def _build(
    monkeypatch,
    *,
    partial: bool = False,
    closed_position: str | None = None,
    applied_valuation_missing: bool = False,
    ledger_passing: bool = True,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed_database(
        database,
        partial=partial,
        closed_position=closed_position,
        applied_valuation_missing=applied_valuation_missing,
    )
    receipt = _receipt(production, database, partial=partial)
    receipt_path = _write(root / "receipt.json", receipt)

    class FakeContinuation:
        @staticmethod
        def _database_path(repo):
            return (Path(repo) / "data" / "pio.db").resolve()

        @staticmethod
        def _database_state(db):
            return {
                "database": _sha(Path(db).read_bytes()),
                "wal": None,
                "shm": None,
            }

    class FakeStorage:
        def __init__(self, path):
            self.path = Path(path)

    class FakeLive:
        Storage = FakeStorage

    class FakeReadiness:
        @staticmethod
        def _load_reviewed(source):
            return (
                FakeContinuation,
                object(),
                object(),
                FakeLive,
                object(),
            )

    class FakeExecutor:
        @staticmethod
        def validate_phase8_recursive_reentry_checkpoint_v8_continuation_evidence_tick_execution_receipt(
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
        return MODULE.build_phase8_recursive_reentry_checkpoint_v8_continuation_evidence_tick_post_audit(
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


def test_complete_open_pair_routes_to_next_tick(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["source_checkpoint_sha256"] == "9" * 64
    assert report["source_post_audit_sha256"] == "2" * 64
    assert report["source_execution_receipt_sha256"] == "3" * 64
    assert report["source_pair_entry_post_audit_sha256"] == "4" * 64
    assert report["pair_entry_request_sha256"] == "5" * 64
    assert report["pair_entry_input_verification_sha256"] == "6" * 64
    assert report["pair_lineage_sha256"] == "7" * 64
    assert report["latest_previous_tick_post_audit_sha256"] == "8" * 64
    assert report["source_final_evaluation_sha256"] == "a" * 64
    assert report["previous_pair_id"] == "pair-1"
    assert report["pair_id"] == "pair-2"
    assert report["previous_tick_observed_at"] == (
        "2026-09-30T09:25:00+00:00"
    )
    assert report["receipt_pair_tick_complete"] is True
    assert report["pair_both_open"] is True
    assert report["pair_any_closed"] is False
    assert report["next_debt_type"] == (
        "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
    )
    assert report["continuation_route"] == (
        "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V8_NEXT_EVIDENCE_TICK_REVIEW"
    )
    assert report["next_evidence_tick_review_ready"] is True
    assert report["tick_recovery_review_ready"] is False
    assert report["terminal_pair_evaluation_ready"] is False
    assert report["ledger_audit_passing"] is True
    assert report["paper_supervisor_tick_authorized"] is False
    assert report["paper_evidence_collection_authorized"] is False
    assert report["continuous_promotion_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_complete_tick_with_closed_leg_routes_terminal(monkeypatch):
    temp, run = _build(
        monkeypatch,
        closed_position="p8-pair-2-challenger",
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["receipt_pair_tick_complete"] is True
    assert report["pair_both_open"] is False
    assert report["pair_any_closed"] is True
    assert report["next_debt_type"] == (
        "PAPER_PAIR_TERMINAL_EVALUATION_REQUIRED"
    )
    assert report["continuation_route"] == (
        "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V8_TERMINAL_EVALUATION_REVIEW"
    )
    assert report["terminal_pair_evaluation_ready"] is True
    assert report["next_evidence_tick_review_ready"] is False
    assert report["tick_recovery_review_ready"] is False


def test_partial_tick_routes_recovery(monkeypatch):
    temp, run = _build(monkeypatch, partial=True)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["receipt_pair_tick_partial_failure"] is True
    assert report["next_debt_type"] == (
        "PAPER_PAIR_TICK_RECOVERY_REQUIRED"
    )
    assert report["continuation_route"] == (
        "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V8_TICK_RECOVERY_REVIEW"
    )
    assert report["tick_recovery_review_ready"] is True
    assert report["next_evidence_tick_review_ready"] is False
    assert report["terminal_pair_evaluation_ready"] is False


def test_applied_item_requires_applied_valuation(monkeypatch):
    temp, run = _build(
        monkeypatch,
        applied_valuation_missing=True,
    )
    try:
        with pytest.raises(
            ValueError,
            match="applied item lacks applied valuation",
        ):
            run()
    finally:
        temp.cleanup()


def test_ledger_failure_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        ledger_passing=False,
    )
    try:
        with pytest.raises(ValueError, match="ledger audit failed"):
            run()
    finally:
        temp.cleanup()


def test_resealed_audit_cannot_authorize_another_tick(monkeypatch):
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
        MODULE.validate_phase8_recursive_reentry_checkpoint_v8_continuation_evidence_tick_post_audit(
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
        MODULE.validate_phase8_recursive_reentry_checkpoint_v8_continuation_evidence_tick_post_audit(
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
        MODULE.validate_phase8_recursive_reentry_checkpoint_v8_continuation_evidence_tick_post_audit(
            report
        )


def test_post_audit_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "apply_paper_chain_valuation(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
