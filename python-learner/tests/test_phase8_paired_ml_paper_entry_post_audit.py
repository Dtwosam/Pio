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
    / "check_phase8_paired_ml_paper_entry_post_audit.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_entry_post_audit",
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
    followup: bool = False,
    valuation: bool = False,
    challenger_policy: str = "ML_CHALLENGER",
) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE paper_accounts(
                account_id TEXT PRIMARY KEY,
                cash_quote TEXT NOT NULL
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
            CREATE TABLE paper_events(
                event_key TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                position_id TEXT,
                event_time TEXT NOT NULL,
                event_type TEXT NOT NULL,
                cash_delta_quote TEXT NOT NULL,
                position_mark_quote TEXT,
                fee_delta_quote TEXT NOT NULL,
                reward_delta_quote TEXT NOT NULL,
                cost_quote TEXT NOT NULL,
                realized_pnl_quote TEXT
            );
            CREATE TABLE paper_counterfactual_positions(
                position_id TEXT PRIMARY KEY,
                pool_address TEXT NOT NULL,
                entry_observed_at TEXT NOT NULL,
                amount_x_atomic TEXT NOT NULL,
                amount_y_atomic TEXT NOT NULL,
                capital_quote TEXT NOT NULL,
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
            "INSERT INTO paper_accounts(account_id, cash_quote) VALUES (?, ?)",
            ("paper-1", "2990"),
        )
        positions = (
            (
                "pair-1-incumbent",
                "ML_CHAMPION",
                "champion-1",
            ),
            (
                "pair-1-challenger",
                challenger_policy,
                "challenger-1",
            ),
        )
        for position_id, policy, model_id in positions:
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
                    ?, 'paper-1', 'pool-1', 'OPEN',
                    ?, ?, 'SPOT', 98, 102,
                    '2026-09-30T00:40:00+00:00', NULL,
                    '1000', '5', '1000', '0', '0', '0', '0', NULL, 0
                )
                """,
                (position_id, policy, model_id),
            )
            event_key = (
                "pair-1-incumbent-enter"
                if policy == "ML_CHAMPION"
                else "pair-1-challenger-enter"
            )
            conn.execute(
                """
                INSERT INTO paper_events(
                    event_key, account_id, position_id, event_time,
                    event_type, cash_delta_quote, position_mark_quote,
                    fee_delta_quote, reward_delta_quote, cost_quote,
                    realized_pnl_quote
                ) VALUES (
                    ?, 'paper-1', ?,
                    '2026-09-30T00:40:00+00:00',
                    'ENTER', '-1005', '1000', '0', '0', '5', NULL
                )
                """,
                (event_key, position_id),
            )
            conn.execute(
                """
                INSERT INTO paper_counterfactual_positions(
                    position_id, pool_address, entry_observed_at,
                    amount_x_atomic, amount_y_atomic, capital_quote,
                    max_share_bps, favor_x_active,
                    token_x_mint, token_y_mint
                ) VALUES (
                    ?, 'pool-1', '2026-09-30T00:40:00+00:00',
                    '10', '20', '1000', 500, 0, 'x-mint', 'y-mint'
                )
                """,
                (position_id,),
            )

        if followup:
            conn.execute(
                """
                INSERT INTO paper_events(
                    event_key, account_id, position_id, event_time,
                    event_type, cash_delta_quote, position_mark_quote,
                    fee_delta_quote, reward_delta_quote, cost_quote,
                    realized_pnl_quote
                ) VALUES (
                    'later-mark', 'paper-1', 'pair-1-challenger',
                    '2026-09-30T00:45:00+00:00',
                    'MARK', '0', '1001', '0', '0', '0', NULL
                )
                """
            )
        if valuation:
            conn.execute(
                """
                INSERT INTO paper_chain_valuations(position_id, observed_at)
                VALUES (
                    'pair-1-challenger',
                    '2026-09-30T00:45:00+00:00'
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


def _position(position_id: str, policy: str, model_id: str) -> dict:
    return {
        "position_id": position_id,
        "account_id": "paper-1",
        "policy_source": policy,
        "model_id": model_id,
        "status": "OPEN",
        "entry_capital_quote": 1000.0,
    }


def _receipt(production: Path, database: Path) -> dict:
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
        "pair_id": "pair-1",
        "pool_address": "pool-1",
        "decision_observed_at": "2026-09-30T00:40:00+00:00",
        "incumbent_position_id": "pair-1-incumbent",
        "challenger_position_id": "pair-1-challenger",
        "incumbent_event_key": "pair-1-incumbent-enter",
        "challenger_event_key": "pair-1-challenger-enter",
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "required_pair_cash_quote": 2010.0,
        "account_cash_quote_after": 2990.0,
        "account_open_positions_after": 2,
        "incumbent_position": _position(
            "pair-1-incumbent",
            "ML_CHAMPION",
            "champion-1",
        ),
        "challenger_position": _position(
            "pair-1-challenger",
            "ML_CHALLENGER",
            "challenger-1",
        ),
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    followup: bool = False,
    valuation: bool = False,
    challenger_policy: str = "ML_CHALLENGER",
    state_override: dict | None = None,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed_database(
        database,
        followup=followup,
        valuation=valuation,
        challenger_policy=challenger_policy,
    )
    receipt = _receipt(production, database)
    receipt_path = _write(root / "receipt.json", receipt)

    class FakeAccount:
        @staticmethod
        def _production_database(repo):
            return (Path(repo) / "data" / "pio.db").resolve()

        @staticmethod
        def _database_state(path):
            if state_override is not None:
                return copy.deepcopy(state_override)
            path = Path(path)
            return {
                "database": _sha(path.read_bytes()),
                "wal": None,
                "shm": None,
            }

    class FakeReadiness:
        @staticmethod
        def _load_reviewed(source):
            return FakeAccount, object(), object(), object()

    class FakeExecutor:
        @staticmethod
        def validate_phase8_paired_ml_paper_entry_execution_receipt(value):
            assert isinstance(value, dict)

        @staticmethod
        def _load_readiness_module(source):
            return FakeReadiness

    monkeypatch.setattr(
        MODULE,
        "_load_executor",
        lambda source: FakeExecutor,
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_entry_post_audit(
            repository=production,
            source_tree=ROOT,
            execution_receipt_path=receipt_path,
        )

    return temp, database, run


def _reseal(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["post_audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_executor_is_exactly_pinned():
    path = ROOT / MODULE.EXECUTOR_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.EXECUTOR_TOOL
    ]


def test_post_audit_verifies_pair_and_stops_before_supervision(
    monkeypatch,
):
    temp, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["receipt_valid"] is True
    assert report["database_matches_receipt"] is True
    assert report["database_unchanged_by_audit"] is True
    assert report["incumbent_position_verified"] is True
    assert report["challenger_position_verified"] is True
    assert report["equal_capital_verified"] is True
    assert report["enter_events_verified"] is True
    assert report["counterfactual_bindings_verified"] is True
    assert report["no_followup_paper_events_verified"] is True
    assert report["no_chain_valuations_applied_verified"] is True
    assert report["cycle_model_binding_verified"] is True
    assert report["pair_seed_ready_for_supervision"] is True
    assert report["next_debt_type"] == (
        "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
    )
    assert report["next_scope"] == "challenger-1"
    assert report["continuation_route"] == (
        "PHASE8_PAIRED_ML_PAPER_SUPERVISION_REVIEW"
    )
    assert report["fresh_chain_state_required_before_supervision"] is True
    assert report["fresh_quote_state_required_before_supervision"] is True
    assert report["separate_supervision_authorization_required"] is True
    assert report["paper_supervisor_tick_authorized"] is False
    assert report["paper_evidence_collection_authorized"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["phase8_promotion_authorized"] is False


def test_followup_paper_event_fails_closed(monkeypatch):
    temp, _, run = _build(monkeypatch, followup=True)
    try:
        with pytest.raises(ValueError, match="follow-up events"):
            run()
    finally:
        temp.cleanup()


def test_chain_valuation_before_audit_fails_closed(monkeypatch):
    temp, _, run = _build(monkeypatch, valuation=True)
    try:
        with pytest.raises(ValueError, match="chain valuations"):
            run()
    finally:
        temp.cleanup()


def test_wrong_challenger_policy_binding_fails_closed(monkeypatch):
    temp, _, run = _build(
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


def test_database_drift_after_receipt_fails_closed(monkeypatch):
    temp, _, run = _build(
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


def test_resealed_audit_cannot_authorize_supervisor_tick(monkeypatch):
    temp, _, run = _build(monkeypatch)
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
        MODULE.validate_phase8_paired_ml_paper_entry_post_audit(report)


def test_resealed_audit_cannot_authorize_live_submit(monkeypatch):
    temp, _, run = _build(monkeypatch)
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
        MODULE.validate_phase8_paired_ml_paper_entry_post_audit(report)


def test_post_audit_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
