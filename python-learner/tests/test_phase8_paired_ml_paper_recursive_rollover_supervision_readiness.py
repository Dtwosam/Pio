from __future__ import annotations

from datetime import datetime, timezone
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
    / "check_phase8_paired_ml_paper_recursive_rollover_supervision_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_recursive_rollover_supervision_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _parse(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def _fmt(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _seed(
    database: Path,
    *,
    latest_chain: str = "2026-09-30T10:15:00+00:00",
    quote_time: str | None = "2026-09-30T10:15:00+00:00",
    challenger_position_status: str = "OPEN",
    challenger_model_status: str = "PAPER_CHALLENGER",
) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.executescript(
            """
            CREATE TABLE paper_positions(
                position_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                pool_address TEXT NOT NULL
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
            VALUES ('p8-pair-4-incumbent', 'OPEN', 'pool-4')
            """
        )
        conn.execute(
            """
            INSERT INTO paper_positions
            VALUES ('p8-pair-4-challenger', ?, 'pool-4')
            """,
            (challenger_position_status,),
        )
        for position_id in (
            "p8-pair-4-incumbent",
            "p8-pair-4-challenger",
        ):
            conn.execute(
                """
                INSERT INTO paper_counterfactual_positions
                VALUES (?, 'x-mint', 'y-mint', NULL, NULL)
                """,
                (position_id,),
            )
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(pool_address, observed_at)
            VALUES ('pool-4', ?)
            """,
            (latest_chain,),
        )
        if quote_time is not None:
            conn.execute(
                """
                INSERT INTO token_quote_observations(
                    token_mint, quote_unit, quote_per_atomic,
                    source, observed_at
                ) VALUES ('y-mint', 'USD', 0.01, 'fixture', ?)
                """,
                (quote_time,),
            )
        conn.execute(
            "INSERT INTO model_registry VALUES ('champion-1', 'CHAMPION')"
        )
        conn.execute(
            "INSERT INTO model_registry VALUES ('challenger-1', ?)",
            (challenger_model_status,),
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


def _audit(production: Path, database: Path) -> dict:
    return {
        "post_audit_sha256": "a" * 64,
        "recursive_rollover_entry_request_sha256": "c" * 64,
        "recursive_rollover_entry_input_verification_sha256": "d" * 64,
        "source_final_evaluation_sha256": "b" * 64,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "audit_database_sha256_after": _sha(database.read_bytes()),
        "audit_wal_sha256_after": None,
        "audit_shm_sha256_after": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-3",
        "pair_id": "pair-4",
        "pool_address": "pool-4",
        "incumbent_position_id": "p8-pair-4-incumbent",
        "challenger_position_id": "p8-pair-4-challenger",
        "decision_observed_at": "2026-09-30T10:10:00+00:00",
        "post_pair_audit_ready": True,
        "recursive_rollover_pair_seed_ready_for_supervision": True,
        "paper_ledger_audit_passing": True,
    }


class _FakeAudit:
    @staticmethod
    def validate_phase8_paired_ml_paper_recursive_rollover_entry_post_audit(value):
        assert isinstance(value, dict)


class _FakeBase:
    @staticmethod
    def _database_path(production):
        return (Path(production) / "data" / "pio.db").resolve()

    @staticmethod
    def _database_state(database):
        database = Path(database)
        return {
            "database": _sha(database.read_bytes()),
            "wal": None,
            "shm": None,
        }

    @staticmethod
    def _parse_time(raw):
        return _parse(raw)

    @staticmethod
    def _format_time(value):
        return _fmt(value)

    @staticmethod
    def _required_quote_mints(conn, position_ids):
        rows = conn.execute(
            """
            SELECT position_id, token_x_mint, token_y_mint,
                   reward_mint_0, reward_mint_1
            FROM paper_counterfactual_positions
            WHERE position_id IN (?, ?)
            """,
            position_ids,
        ).fetchall()
        if len(rows) != 2:
            raise ValueError("counterfactual bindings incomplete")
        return ("y-mint",)

    @staticmethod
    def _quote_status(
        conn,
        *,
        token_mint,
        as_of,
        max_age_seconds,
    ):
        row = conn.execute(
            """
            SELECT quote_per_atomic, source, observed_at
            FROM token_quote_observations
            WHERE token_mint=? AND quote_unit='USD'
            ORDER BY id DESC LIMIT 1
            """,
            (token_mint,),
        ).fetchone()
        if row is None:
            return {
                "token_mint": token_mint,
                "available": False,
                "fresh": False,
                "quote_per_atomic": None,
                "source": None,
                "observed_at": None,
                "age_seconds": None,
                "reason": "no quote",
            }
        observed = _parse(str(row[2]))
        age = max(0, int((as_of - observed).total_seconds()))
        return {
            "token_mint": token_mint,
            "available": True,
            "fresh": age <= max_age_seconds and float(row[0]) > 0,
            "quote_per_atomic": float(row[0]),
            "source": str(row[1]),
            "observed_at": str(row[2]),
            "age_seconds": age,
            "reason": None,
        }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    latest_chain: str = "2026-09-30T10:15:00+00:00",
    quote_time: str | None = "2026-09-30T10:15:00+00:00",
    challenger_position_status: str = "OPEN",
    challenger_model_status: str = "PAPER_CHALLENGER",
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
        challenger_position_status=challenger_position_status,
        challenger_model_status=challenger_model_status,
    )
    audit_path = _write(
        root / "audit.json",
        _audit(production, database),
    )
    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (_FakeAudit, _FakeBase),
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_recursive_rollover_supervision_readiness(
            repository=production,
            source_tree=ROOT,
            recursive_rollover_post_audit_path=audit_path,
            as_of="2026-09-30T10:16:00+00:00",
            chain_max_age_seconds=300,
            quote_max_age_seconds=300,
        )

    return temp, run


def _reseal(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["readiness_sha256"] = _sha(
        MODULE._canonical_bytes(identity)
    )


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_recursive_rollover_supervision_ready_on_new_chain_and_fresh_quotes(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["previous_pair_id"] == "pair-3"
    assert report["pair_id"] == "pair-4"
    assert report["requested_position_ids"] == [
        "p8-pair-4-incumbent",
        "p8-pair-4-challenger",
    ]
    assert report["recursive_rollover_entry_request_sha256"] == "c" * 64
    assert report["recursive_rollover_entry_input_verification_sha256"] == "d" * 64
    assert report["chain_newer_than_entry"] is True
    assert report["chain_fresh"] is True
    assert report["quotes_ready"] is True
    assert report["readiness_status"] == "READY"
    assert report["recursive_rollover_evidence_tick_request_ready"] is True
    assert report["paper_supervisor_tick_authorized"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_recursive_rollover_supervision_waits_for_new_chain(monkeypatch):
    temp, run = _build(
        monkeypatch,
        latest_chain="2026-09-30T10:10:00+00:00",
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["chain_newer_than_entry"] is False
    assert report["readiness_status"] == "WAITING_NEW_CHAIN"
    assert report["recursive_rollover_evidence_tick_request_ready"] is False


def test_recursive_rollover_supervision_waits_for_quotes(monkeypatch):
    temp, run = _build(monkeypatch, quote_time=None)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["chain_newer_than_entry"] is True
    assert report["quotes_ready"] is False
    assert report["readiness_status"] == "WAITING_QUOTES"
    assert report["recursive_rollover_evidence_tick_request_ready"] is False


def test_recursive_rollover_supervision_refuses_closed_pair_leg(monkeypatch):
    temp, run = _build(
        monkeypatch,
        challenger_position_status="CLOSED",
    )
    try:
        with pytest.raises(ValueError, match="no longer open/bound"):
            run()
    finally:
        temp.cleanup()


def test_recursive_rollover_supervision_refuses_model_status_drift(monkeypatch):
    temp, run = _build(
        monkeypatch,
        challenger_model_status="ROLLED_BACK",
    )
    try:
        with pytest.raises(ValueError, match="cycle/model binding changed"):
            run()
    finally:
        temp.cleanup()


def test_recursive_rollover_supervision_refuses_pair_id_regression(monkeypatch):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed(database)
    audit = _audit(production, database)
    audit["pair_id"] = audit["previous_pair_id"]
    audit_path = _write(root / "audit.json", audit)
    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (_FakeAudit, _FakeBase),
    )
    try:
        with pytest.raises(ValueError, match="pair id was not advanced"):
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_supervision_readiness(
                repository=production,
                source_tree=ROOT,
                recursive_rollover_post_audit_path=audit_path,
                as_of="2026-09-30T10:16:00+00:00",
            )
    finally:
        temp.cleanup()


def test_resealed_readiness_cannot_authorize_supervisor(monkeypatch):
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
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_supervision_readiness(
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
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_supervision_readiness(
            report
        )


def test_recursive_rollover_supervision_readiness_has_no_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
