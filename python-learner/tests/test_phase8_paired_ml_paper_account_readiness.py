from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sqlite3
import tempfile

import pytest

from importlib import util


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase8_paired_ml_paper_account_readiness.py"
)

SPEC = util.spec_from_file_location(
    "check_phase8_paired_ml_paper_account_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _verification(
    *,
    mode: str = "EXISTING",
    starting_cash: float | None = None,
) -> dict:
    if mode == "CREATE" and starting_cash is None:
        starting_cash = 10000.0
    return {
        "verification_sha256": "a" * 64,
        "source_post_audit_sha256": "b" * 64,
        "source_paper_evidence_input_verification_sha256": "c" * 64,
        "production_repository": "",
        "pio_database_path": "",
        "model_id": "challenger-1",
        "active_cycle_id": "cycle-1",
        "account_mode": mode,
        "account_id": "paper-1",
        "starting_cash_quote": starting_cash,
        "pair_id": "pair-001",
        "pool_address": "pool-1",
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "incumbent_position_id": "p8-pair-001-incumbent",
        "challenger_position_id": "p8-pair-001-challenger",
        "incumbent_event_key": "p8-pair-001:incumbent",
        "challenger_event_key": "p8-pair-001:challenger",
        "paired_entry_inputs_ready": True,
    }


class FakePairInputs:
    verification = _verification()

    @classmethod
    def verify_phase8_paired_ml_paper_entry_inputs(cls, **kwargs):
        return copy.deepcopy(cls.verification)


def _seed(
    database: Path,
    *,
    account: bool = True,
    account_starting: float = 10000.0,
    cash: float = 10000.0,
    conflict: bool = False,
) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.executescript(
            """
            CREATE TABLE model_registry(
                model_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            );
            CREATE TABLE continuous_learning_cycles(
                cycle_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                active_key TEXT,
                champion_model_id TEXT NOT NULL,
                challenger_model_id TEXT
            );
            CREATE TABLE paper_accounts(
                account_id TEXT PRIMARY KEY,
                starting_equity_quote REAL NOT NULL,
                cash_quote REAL NOT NULL
            );
            CREATE TABLE paper_positions(
                position_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE paper_events(
                event_key TEXT PRIMARY KEY
            );
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
            INSERT INTO continuous_learning_cycles
            VALUES (
                'cycle-1', 'PAPER_CHALLENGER', 'ACTIVE',
                'champion-1', 'challenger-1'
            )
            """
        )
        if account:
            conn.execute(
                "INSERT INTO paper_accounts VALUES ('paper-1', ?, ?)",
                (account_starting, cash),
            )
        if conflict:
            conn.execute(
                """
                INSERT INTO paper_positions
                VALUES ('p8-pair-001-incumbent', 'paper-1', 'OPEN')
                """
            )
        conn.commit()
    finally:
        conn.close()


def _build(
    monkeypatch,
    *,
    mode: str = "EXISTING",
    account: bool = True,
    account_starting: float = 10000.0,
    cash: float = 10000.0,
    conflict: bool = False,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed(
        database,
        account=account,
        account_starting=account_starting,
        cash=cash,
        conflict=conflict,
    )
    FakePairInputs.verification = _verification(mode=mode)
    FakePairInputs.verification["production_repository"] = str(production)
    FakePairInputs.verification["pio_database_path"] = str(database.resolve())
    monkeypatch.setattr(
        MODULE,
        "_load_pair_input_module",
        lambda source: FakePairInputs,
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_account_readiness(
            repository=production,
            source_tree=ROOT,
            post_audit_path=root / "post-audit.json",
            paper_evidence_input_path=root / "paper-inputs.json",
            paired_entry_input_path=root / "pair-inputs.json",
        )

    return temp, database, run


def test_reviewed_pair_input_tool_is_exactly_pinned():
    path = ROOT / MODULE.PAIR_INPUT_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.PAIR_INPUT_TOOL
    ]


def test_existing_account_with_cash_is_ready_and_read_only(monkeypatch):
    temp, database, run = _build(monkeypatch)
    try:
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        report = run()
        after = hashlib.sha256(database.read_bytes()).hexdigest()
    finally:
        temp.cleanup()

    assert before == after
    assert report["readiness_status"] == MODULE.STATUS_READY
    assert report["account_exists"] is True
    assert report["paper_cash_sufficient_for_pair"] is True
    assert report["pair_position_ids_available"] is True
    assert report["pair_event_keys_available"] is True
    assert report["cycle_model_binding_valid"] is True
    assert report["account_creation_required"] is False
    assert report["account_ready"] is True
    assert report["paired_entry_preconditions_ready"] is True
    assert report["paper_pair_entry_authorized"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["production_pio_database_modified"] is False


def test_missing_create_account_reports_separate_creation_dependency(
    monkeypatch,
):
    temp, _, run = _build(
        monkeypatch,
        mode="CREATE",
        account=False,
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["readiness_status"] == (
        MODULE.STATUS_ACCOUNT_CREATION_REQUIRED
    )
    assert report["account_exists"] is False
    assert report["account_creation_required"] is True
    assert report["account_ready"] is False
    assert report["paired_entry_preconditions_ready"] is False
    assert report[
        "requires_separate_account_creation_authorization"
    ] is True
    assert report["paper_account_creation_authorized"] is False


def test_missing_existing_account_fails_readiness_without_creation(
    monkeypatch,
):
    temp, _, run = _build(
        monkeypatch,
        mode="EXISTING",
        account=False,
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["readiness_status"] == (
        MODULE.STATUS_EXISTING_ACCOUNT_MISSING
    )
    assert report["account_creation_required"] is False
    assert report[
        "requires_separate_account_creation_authorization"
    ] is False
    assert report["account_ready"] is False


def test_create_account_starting_equity_mismatch_fails_readiness(
    monkeypatch,
):
    temp, _, run = _build(
        monkeypatch,
        mode="CREATE",
        account=True,
        account_starting=5000.0,
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["readiness_status"] == MODULE.STATUS_ACCOUNT_MISMATCH
    assert report["starting_equity_matches_request"] is False
    assert report["account_ready"] is False


def test_insufficient_pair_cash_fails_readiness(monkeypatch):
    temp, _, run = _build(
        monkeypatch,
        cash=2000.0,
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["required_pair_cash_quote"] == 2010.0
    assert report["paper_cash_sufficient_for_pair"] is False
    assert report["readiness_status"] == MODULE.STATUS_INSUFFICIENT_CASH
    assert report["account_ready"] is False


def test_pair_position_id_conflict_fails_readiness(monkeypatch):
    temp, _, run = _build(
        monkeypatch,
        conflict=True,
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["pair_position_ids_available"] is False
    assert report["readiness_status"] == MODULE.STATUS_ID_CONFLICT
    assert report["account_ready"] is False


def test_cycle_model_drift_fails_closed(monkeypatch):
    temp, database, run = _build(monkeypatch)
    try:
        conn = sqlite3.connect(database)
        try:
            conn.execute(
                """
                UPDATE continuous_learning_cycles
                SET status = 'OFFLINE_QUALIFIED'
                WHERE cycle_id = 'cycle-1'
                """
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ValueError, match="no longer valid"):
            run()
    finally:
        temp.cleanup()


def test_account_readiness_tool_has_no_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "create_paper_account(" not in source
    assert "open_paired_ml_paper_entries(" not in source
    assert "UPDATE paper_accounts" not in source
    assert "INSERT INTO paper_accounts" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_account_creation_authorized": False' in source
    assert '"paper_pair_entry_authorized": False' in source
    assert '"live_submit_authorized": False' in source
