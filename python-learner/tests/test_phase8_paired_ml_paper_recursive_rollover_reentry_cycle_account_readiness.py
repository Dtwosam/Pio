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
    / "check_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_account_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_account_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verification() -> dict:
    return {
        "verification_sha256": "a" * 64,
        "source_final_evaluation_sha256": "b" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "c" * 64,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-4",
        "pair_id": "pair-5",
        "pool_address": "pool-5",
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "incumbent_position_id": "p8-pair-5-incumbent",
        "challenger_position_id": "p8-pair-5-challenger",
        "incumbent_event_key": "p8-pair-5:incumbent",
        "challenger_event_key": "p8-pair-5:challenger",
        "reentry_cycle_inputs_ready": True,
    }


def _final() -> dict:
    return {
        "evaluation_sha256": "b" * 64,
        "incumbent_position_id": "p8-pair-4-incumbent",
        "challenger_position_id": "p8-pair-4-challenger",
    }


def _seed(
    database: Path,
    *,
    cash: float = 5000.0,
    extra_open: bool = False,
    previous_challenger_status: str = "CLOSED",
    id_conflict: bool = False,
) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.executescript(
            """
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
                event_key TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                position_id TEXT
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
            """
            INSERT INTO paper_accounts
            VALUES ('paper-1', 10000.0, ?)
            """,
            (cash,),
        )
        conn.execute(
            """
            INSERT INTO paper_positions
            VALUES ('p8-pair-4-incumbent', 'paper-1', 'CLOSED')
            """
        )
        conn.execute(
            """
            INSERT INTO paper_positions
            VALUES ('p8-pair-4-challenger', 'paper-1', ?)
            """,
            (previous_challenger_status,),
        )
        if extra_open:
            conn.execute(
                """
                INSERT INTO paper_positions
                VALUES ('other-open', 'paper-1', 'OPEN')
                """
            )
        if id_conflict:
            conn.execute(
                """
                INSERT INTO paper_positions
                VALUES ('p8-pair-5-incumbent', 'paper-1', 'CLOSED')
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
            VALUES(
                'cycle-1', 'PAPER_CHALLENGER', 'ACTIVE',
                'champion-1', 'challenger-1'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


class _FakeRecursive:
    @staticmethod
    def verify_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_inputs(
        *,
        source_tree,
        final_evaluation_path,
        input_path,
    ):
        value = _verification()
        production = Path(
            json.loads(Path(input_path).read_text())["production_repository"]
        )
        database = (production / "data" / "pio.db").resolve()
        value["production_repository"] = str(production)
        value["pio_database_path"] = str(database)
        return value

    @staticmethod
    def _base(source, final_evaluation_path):
        return object(), _final(), {}


class _FakeAccount:
    @staticmethod
    def _production_database(production):
        return (Path(production) / "data" / "pio.db").resolve()

    @staticmethod
    def _database_state(database):
        return {
            "database": _sha(Path(database)),
            "wal": None,
            "shm": None,
        }

    @staticmethod
    def _account_state(conn, account_id):
        row = conn.execute(
            """
            SELECT starting_equity_quote, cash_quote
            FROM paper_accounts
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        open_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM paper_positions
            WHERE account_id = ? AND status = 'OPEN'
            """,
            (account_id,),
        ).fetchone()[0]
        closed_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM paper_positions
            WHERE account_id = ? AND status = 'CLOSED'
            """,
            (account_id,),
        ).fetchone()[0]
        return {
            "starting_equity_quote": float(row[0]),
            "cash_quote": float(row[1]),
            "open_positions": int(open_count),
            "closed_positions": int(closed_count),
        }

    @staticmethod
    def _pair_ids_available(
        conn,
        *,
        incumbent_position_id,
        challenger_position_id,
        incumbent_event_key,
        challenger_event_key,
    ):
        position_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM paper_positions
            WHERE position_id IN (?, ?)
            """,
            (incumbent_position_id, challenger_position_id),
        ).fetchone()[0]
        event_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM paper_events
            WHERE event_key IN (?, ?)
            """,
            (incumbent_event_key, challenger_event_key),
        ).fetchone()[0]
        return position_count == 0, event_count == 0

    @staticmethod
    def _cycle_state(conn, cycle_id):
        row = conn.execute(
            """
            SELECT status, active_key, champion_model_id, challenger_model_id
            FROM continuous_learning_cycles
            WHERE cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()
        assert row is not None
        statuses = dict(conn.execute(
            "SELECT model_id, status FROM model_registry"
        ).fetchall())
        return {
            "cycle_status": str(row[0]),
            "cycle_active_key": str(row[1]),
            "incumbent_model_id": str(row[2]),
            "incumbent_model_status": statuses[str(row[2])],
            "challenger_model_id": str(row[3]),
            "challenger_model_status": statuses[str(row[3])],
        }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    cash: float = 5000.0,
    extra_open: bool = False,
    previous_challenger_status: str = "CLOSED",
    id_conflict: bool = False,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed(
        database,
        cash=cash,
        extra_open=extra_open,
        previous_challenger_status=previous_challenger_status,
        id_conflict=id_conflict,
    )
    final_path = _write(root / "final.json", _final())
    input_path = _write(
        root / "input.json",
        {"production_repository": str(production)},
    )

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (_FakeRecursive, _FakeAccount),
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_account_readiness(
            repository=production,
            source_tree=ROOT,
            final_evaluation_path=final_path,
            recursive_rollover_reentry_cycle_input_path=input_path,
        )

    return temp, run


def _reseal(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_reentry_cycle_account_ready_only_with_clean_sequential_state(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["previous_pair_positions_closed"] is True
    assert report["previous_pair_id"] == "pair-4"
    assert report["pair_id"] == "pair-5"
    assert report["recursive_rollover_reentry_cycle_input_verification_sha256"] == "a" * 64
    assert report["account_exists"] is True
    assert report["account_open_positions"] == 0
    assert report["zero_open_positions_verified"] is True
    assert report["paper_cash_sufficient_for_pair"] is True
    assert report["pair_position_ids_available"] is True
    assert report["pair_event_keys_available"] is True
    assert report["readiness_status"] == "READY"
    assert report["account_ready"] is True
    assert report["reentry_cycle_preconditions_ready"] is True
    assert report["paper_pair_entry_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_open_position_blocks_reentry_cycle_pair(monkeypatch):
    temp, run = _build(monkeypatch, extra_open=True)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["account_open_positions"] == 1
    assert report["zero_open_positions_verified"] is False
    assert report["readiness_status"] == "OPEN_POSITIONS_PRESENT"
    assert report["account_ready"] is False


def test_insufficient_cash_blocks_reentry_cycle_pair(monkeypatch):
    temp, run = _build(monkeypatch, cash=1000.0)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["paper_cash_sufficient_for_pair"] is False
    assert report["readiness_status"] == "INSUFFICIENT_CASH"
    assert report["account_ready"] is False


def test_previous_pair_drift_blocks_reentry_cycle_pair(monkeypatch):
    temp, run = _build(
        monkeypatch,
        previous_challenger_status="OPEN",
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["previous_pair_positions_closed"] is False
    assert report["readiness_status"] == "PREVIOUS_PAIR_DRIFT"
    assert report["account_ready"] is False


def test_pair_id_conflict_blocks_reentry_cycle_pair(monkeypatch):
    temp, run = _build(monkeypatch, id_conflict=True)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["pair_position_ids_available"] is False
    assert report["readiness_status"] == "ID_CONFLICT"
    assert report["account_ready"] is False


def test_resealed_readiness_cannot_authorize_pair_open(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_pair_entry_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_pair_entry_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_account_readiness(
            report
        )


def test_resealed_reentry_cycle_readiness_cannot_authorize_promotion(monkeypatch):
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
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_account_readiness(
            report
        )


def test_reentry_cycle_account_readiness_has_no_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "open_paired_ml_paper_entries(" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert '"paper_pair_entry_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
