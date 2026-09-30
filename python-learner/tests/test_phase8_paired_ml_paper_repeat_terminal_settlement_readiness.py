from __future__ import annotations

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
    / "check_phase8_paired_ml_paper_repeat_terminal_settlement_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_repeat_terminal_settlement_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _terminal(database: Path) -> dict:
    return {
        "evaluation_sha256": "a" * 64,
        "source_terminal_post_audit_sha256": "b" * 64,
        "source_repeat_post_audit_sha256": "c" * 64,
        "source_final_evaluation_sha256": "d" * 64,
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
        "pair_settlement_review_ready": True,
        "pair_has_open_remainder": True,
        "next_debt_type": "PAPER_PAIR_SETTLEMENT_REQUIRED",
        "paper_pair_settlement_authorized": False,
    }


class _FakeTerminal:
    DEBT_PAIR_SETTLEMENT = "PAPER_PAIR_SETTLEMENT_REQUIRED"

    @staticmethod
    def validate_phase8_paired_ml_paper_repeat_terminal_evaluation(value):
        assert isinstance(value, dict)


def _seed(
    database: Path,
    *,
    latest_chain: str = "2026-09-30T09:35:00+00:00",
    quote_time: str | None = "2026-09-30T09:35:00+00:00",
    incumbent_status: str = "CLOSED",
    challenger_status: str = "OPEN",
    challenger_model_id: str = "challenger-1",
) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.executescript(
            """
            CREATE TABLE paper_positions(
                position_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                policy_source TEXT NOT NULL,
                model_id TEXT NOT NULL
            );
            CREATE TABLE paper_counterfactual_positions(
                position_id TEXT PRIMARY KEY,
                token_x_mint TEXT NOT NULL,
                token_y_mint TEXT NOT NULL,
                reward_mint_0 TEXT,
                reward_mint_1 TEXT
            );
            CREATE TABLE chain_pool_snapshots(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );
            CREATE TABLE token_quote_observations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_mint TEXT NOT NULL,
                quote_unit TEXT NOT NULL,
                quote_per_atomic REAL NOT NULL,
                source TEXT NOT NULL,
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
            """
            INSERT INTO paper_positions
            VALUES ('p8-pair-2-incumbent', ?, 'pool-2', 'ML_CHAMPION', 'champion-1')
            """,
            (incumbent_status,),
        )
        conn.execute(
            """
            INSERT INTO paper_positions
            VALUES ('p8-pair-2-challenger', ?, 'pool-2', 'ML_CHALLENGER', ?)
            """,
            (challenger_status, challenger_model_id),
        )
        conn.execute(
            """
            INSERT INTO paper_counterfactual_positions
            VALUES ('p8-pair-2-incumbent', 'x-mint', 'y-mint', NULL, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO paper_counterfactual_positions
            VALUES ('p8-pair-2-challenger', 'x-mint', 'y-mint', NULL, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(pool_address, observed_at)
            VALUES ('pool-2', ?)
            """,
            (latest_chain,),
        )
        if quote_time is not None:
            conn.execute(
                """
                INSERT INTO token_quote_observations(
                    token_mint, quote_unit, quote_per_atomic, source, observed_at
                ) VALUES ('y-mint', 'USD', 0.01, 'fixture', ?)
                """,
                (quote_time,),
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


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    latest_chain: str = "2026-09-30T09:35:00+00:00",
    quote_time: str | None = "2026-09-30T09:35:00+00:00",
    incumbent_status: str = "CLOSED",
    challenger_status: str = "OPEN",
    challenger_model_id: str = "challenger-1",
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed(
        database,
        latest_chain=latest_chain,
        quote_time=quote_time,
        incumbent_status=incumbent_status,
        challenger_status=challenger_status,
        challenger_model_id=challenger_model_id,
    )
    terminal = _terminal(database)
    terminal["pio_database_sha256_after"] = _sha(database.read_bytes())
    terminal_path = _write(root / "terminal.json", terminal)

    monkeypatch.setattr(
        MODULE,
        "_load_terminal_evaluation",
        lambda source: _FakeTerminal,
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_repeat_terminal_settlement_readiness(
            repository=production,
            source_tree=ROOT,
            terminal_evaluation_path=terminal_path,
            as_of="2026-09-30T09:36:00+00:00",
            chain_max_age_seconds=300,
            quote_max_age_seconds=300,
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


def test_reviewed_terminal_evaluation_is_exactly_pinned():
    path = ROOT / MODULE.TERMINAL_EVALUATION_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.TERMINAL_EVALUATION_TOOL
    ]


def test_single_open_leg_becomes_ready_with_new_chain_and_quote(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["open_position_id"] == "p8-pair-2-challenger"
    assert report["closed_position_id"] == "p8-pair-2-incumbent"
    assert report["open_position_policy_source"] == "ML_CHALLENGER"
    assert report["open_position_model_id"] == "challenger-1"
    assert report["previous_pair_id"] == "pair-1"
    assert report["pair_id"] == "pair-2"
    assert report["source_repeat_post_audit_sha256"] == "c" * 64
    assert report["source_final_evaluation_sha256"] == "d" * 64
    assert report["entry_observed_at"] == "2026-09-30T09:20:00+00:00"
    assert report["chain_newer_than_terminal_tick"] is True
    assert report["chain_fresh"] is True
    assert report["quotes_ready"] is True
    assert report["readiness_status"] == "READY"
    assert report["settlement_tick_request_ready"] is True
    assert report["explicit_single_position_scope_required"] is True
    assert report["paper_settlement_tick_authorized"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_waits_for_newer_chain(monkeypatch):
    temp, run = _build(
        monkeypatch,
        latest_chain="2026-09-30T09:30:00+00:00",
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["chain_newer_than_terminal_tick"] is False
    assert report["readiness_status"] == "WAITING_NEW_CHAIN"
    assert report["settlement_tick_request_ready"] is False


def test_waits_for_required_quote(monkeypatch):
    temp, run = _build(monkeypatch, quote_time=None)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["chain_newer_than_terminal_tick"] is True
    assert report["quotes_ready"] is False
    assert report["readiness_status"] == "WAITING_QUOTES"
    assert report["settlement_tick_request_ready"] is False


def test_refuses_if_closed_leg_reopens(monkeypatch):
    temp, run = _build(
        monkeypatch,
        incumbent_status="OPEN",
        challenger_status="OPEN",
    )
    try:
        with pytest.raises(
            ValueError,
            match="one OPEN and one CLOSED leg",
        ):
            run()
    finally:
        temp.cleanup()


def test_refuses_open_leg_model_drift(monkeypatch):
    temp, run = _build(
        monkeypatch,
        challenger_model_id="challenger-2",
    )
    try:
        with pytest.raises(ValueError, match="open model binding changed"):
            run()
    finally:
        temp.cleanup()


def test_refuses_repeat_pair_id_regression(monkeypatch):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed(database)
    terminal = _terminal(database)
    terminal["pair_id"] = terminal["previous_pair_id"]
    terminal["pio_database_sha256_after"] = _sha(database.read_bytes())
    terminal_path = _write(root / "terminal.json", terminal)
    monkeypatch.setattr(
        MODULE,
        "_load_terminal_evaluation",
        lambda source: _FakeTerminal,
    )
    try:
        with pytest.raises(ValueError, match="pair id was not advanced"):
            MODULE.build_phase8_paired_ml_paper_repeat_terminal_settlement_readiness(
                repository=production,
                source_tree=ROOT,
                terminal_evaluation_path=terminal_path,
                as_of="2026-09-30T09:36:00+00:00",
            )
    finally:
        temp.cleanup()


def test_resealed_readiness_cannot_authorize_settlement_tick(monkeypatch):
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
        MODULE.validate_phase8_paired_ml_paper_repeat_terminal_settlement_readiness(
            report
        )


def test_resealed_readiness_cannot_authorize_live_submit(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["live_submit_authorized"] = True
    _reseal(report)
    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase8_paired_ml_paper_repeat_terminal_settlement_readiness(
            report
        )


def test_resealed_readiness_cannot_authorize_promotion(monkeypatch):
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
        MODULE.validate_phase8_paired_ml_paper_repeat_terminal_settlement_readiness(
            report
        )


def test_settlement_readiness_has_no_paper_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "apply_paper_chain_valuation(" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_settlement_tick_authorized": False' in source
    assert '"paper_settlement_tick_executed": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
