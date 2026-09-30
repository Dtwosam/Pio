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
    / "check_phase8_paired_ml_paper_terminal_settlement_continuation_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_terminal_settlement_continuation_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _audit(database: Path) -> dict:
    return {
        "post_audit_sha256": "a" * 64,
        "pio_database_path": str(database),
        "audit_database_sha256_after": _sha(database.read_bytes()),
        "audit_wal_sha256_after": None,
        "audit_shm_sha256_after": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "pair_id": "pair-1",
        "pool_address": "pool-1",
        "incumbent_position_id": "pair-1-incumbent",
        "challenger_position_id": "pair-1-challenger",
        "open_position_id": "pair-1-challenger",
        "closed_position_id": "pair-1-incumbent",
        "open_position_policy_source": "ML_CHALLENGER",
        "open_position_model_id": "challenger-1",
        "target_chain_observed_at": "2026-09-30T08:05:00+00:00",
        "next_settlement_tick_review_ready": True,
        "settlement_recovery_review_ready": False,
        "final_pair_evaluation_ready": False,
        "open_leg_still_open": True,
        "receipt_settlement_tick_complete": True,
    }


class _FakeAudit:
    @staticmethod
    def validate_phase8_paired_ml_paper_terminal_settlement_tick_post_audit(
        value,
    ):
        assert isinstance(value, dict)


def _seed(
    database: Path,
    *,
    latest_chain: str = "2026-09-30T08:10:00+00:00",
    quote_time: str | None = "2026-09-30T08:10:00+00:00",
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
            VALUES (
                'pair-1-incumbent', 'CLOSED', 'pool-1',
                'ML_CHAMPION', 'champion-1'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_positions
            VALUES (
                'pair-1-challenger', 'OPEN', 'pool-1',
                'ML_CHALLENGER', 'challenger-1'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_counterfactual_positions
            VALUES ('pair-1-challenger', 'x-mint', 'y-mint', NULL, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(pool_address, observed_at)
            VALUES ('pool-1', ?)
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
    latest_chain: str = "2026-09-30T08:10:00+00:00",
    quote_time: str | None = "2026-09-30T08:10:00+00:00",
    recovery: bool = False,
    final: bool = False,
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
    )
    audit = _audit(database)
    audit["audit_database_sha256_after"] = _sha(database.read_bytes())
    if recovery:
        audit["next_settlement_tick_review_ready"] = False
        audit["settlement_recovery_review_ready"] = True
    if final:
        audit["next_settlement_tick_review_ready"] = False
        audit["final_pair_evaluation_ready"] = True
        audit["open_leg_still_open"] = False
    path = _write(root / "audit.json", audit)

    monkeypatch.setattr(
        MODULE,
        "_load_previous_audit",
        lambda source: _FakeAudit,
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_terminal_settlement_continuation_readiness(
            repository=production,
            source_tree=ROOT,
            previous_settlement_post_audit_path=path,
            as_of="2026-09-30T08:11:00+00:00",
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


def test_reviewed_previous_settlement_audit_is_exactly_pinned():
    path = ROOT / MODULE.PREVIOUS_SETTLEMENT_AUDIT_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.PREVIOUS_SETTLEMENT_AUDIT_TOOL
    ]


def test_ready_requires_chain_newer_than_previous_settlement_tick(
    monkeypatch,
):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["previous_settlement_tick_observed_at"] == (
        "2026-09-30T08:05:00+00:00"
    )
    assert report["latest_chain_observed_at"] == (
        "2026-09-30T08:10:00+00:00"
    )
    assert report["chain_newer_than_previous_settlement_tick"] is True
    assert report["quotes_ready"] is True
    assert report["readiness_status"] == "READY"
    assert report["continuation_settlement_tick_request_ready"] is True
    assert report["paper_settlement_tick_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_same_chain_snapshot_waits(monkeypatch):
    temp, run = _build(
        monkeypatch,
        latest_chain="2026-09-30T08:05:00+00:00",
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["chain_newer_than_previous_settlement_tick"] is False
    assert report["readiness_status"] == "WAITING_NEW_CHAIN"
    assert report["continuation_settlement_tick_request_ready"] is False


def test_missing_quote_waits(monkeypatch):
    temp, run = _build(monkeypatch, quote_time=None)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["quotes_ready"] is False
    assert report["readiness_status"] == "WAITING_QUOTES"


def test_recovery_state_is_refused(monkeypatch):
    temp, run = _build(monkeypatch, recovery=True)
    try:
        with pytest.raises(ValueError, match="does not route to continuation"):
            run()
    finally:
        temp.cleanup()


def test_final_state_is_refused(monkeypatch):
    temp, run = _build(monkeypatch, final=True)
    try:
        with pytest.raises(ValueError, match="does not route to continuation"):
            run()
    finally:
        temp.cleanup()


def test_resealed_readiness_cannot_authorize_tick(monkeypatch):
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
        MODULE.validate_phase8_paired_ml_paper_terminal_settlement_continuation_readiness(
            report
        )


def test_continuation_readiness_has_no_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_live_chain_paper_batch(" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_settlement_tick_authorized": False' in source
    assert '"live_submit_authorized": False' in source
