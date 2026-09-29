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
    / "check_phase8_paired_ml_paper_supervision_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_supervision_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _audit(production: Path, database: Path) -> dict:
    return {
        "post_audit_sha256": "a" * 64,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "pair_id": "pair-1",
        "pool_address": "pool-1",
        "decision_observed_at": "2026-09-30T00:40:00+00:00",
        "incumbent_position_id": "pair-1-incumbent",
        "challenger_position_id": "pair-1-challenger",
        "post_pair_audit_ready": True,
        "pair_seed_ready_for_supervision": True,
        "paper_supervisor_tick_authorized": False,
    }


def _seed(
    database: Path,
    *,
    latest_chain: str | None = "2026-09-30T00:44:00+00:00",
    quote_time: str | None = "2026-09-30T00:44:30+00:00",
    quote_value: str = "0.0001",
    extra_reward: bool = False,
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
                quote_per_atomic TEXT NOT NULL,
                source TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );
            """
        )
        for position_id in (
            "pair-1-incumbent",
            "pair-1-challenger",
        ):
            conn.execute(
                """
                INSERT INTO paper_positions(
                    position_id, status, pool_address
                ) VALUES (?, 'OPEN', 'pool-1')
                """,
                (position_id,),
            )
            conn.execute(
                """
                INSERT INTO paper_counterfactual_positions(
                    position_id, token_x_mint, token_y_mint,
                    reward_mint_0, reward_mint_1
                ) VALUES (?, 'x-mint', 'y-mint', ?, NULL)
                """,
                (
                    position_id,
                    "reward-mint" if extra_reward else None,
                ),
            )

        if latest_chain is not None:
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
                    token_mint, quote_unit, quote_per_atomic,
                    source, observed_at
                ) VALUES ('y-mint', 'USD', ?, 'fixture', ?)
                """,
                (quote_value, quote_time),
            )
            if extra_reward:
                conn.execute(
                    """
                    INSERT INTO token_quote_observations(
                        token_mint, quote_unit, quote_per_atomic,
                        source, observed_at
                    ) VALUES (
                        'reward-mint', 'USD', '0.0002',
                        'fixture', ?
                    )
                    """,
                    (quote_time,),
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
    latest_chain="2026-09-30T00:44:00+00:00",
    quote_time="2026-09-30T00:44:30+00:00",
    quote_value="0.0001",
    extra_reward=False,
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
        quote_value=quote_value,
        extra_reward=extra_reward,
    )
    audit_path = _write(
        root / "audit.json",
        _audit(production, database),
    )

    class FakeAccount:
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

    class FakeReadiness:
        @staticmethod
        def _load_reviewed(source):
            return FakeAccount, object(), object(), object()

    class FakeExecutor:
        @staticmethod
        def _load_readiness_module(source):
            return FakeReadiness

    class FakeAudit:
        @staticmethod
        def validate_phase8_paired_ml_paper_entry_post_audit(value):
            assert isinstance(value, dict)

        @staticmethod
        def _load_executor(source):
            return FakeExecutor

    monkeypatch.setattr(
        MODULE,
        "_load_post_audit",
        lambda source: FakeAudit,
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_supervision_readiness(
            repository=production,
            source_tree=ROOT,
            post_audit_path=audit_path,
            as_of="2026-09-30T00:45:00+00:00",
        )

    return temp, database, run


def _reseal(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_post_audit_is_exactly_pinned():
    path = ROOT / MODULE.POST_AUDIT_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.POST_AUDIT_TOOL
    ]


def test_ready_when_new_chain_and_quotes_are_fresh(monkeypatch):
    temp, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["readiness_status"] == "READY"
    assert report["chain_newer_than_entry"] is True
    assert report["chain_fresh"] is True
    assert report["quotes_ready"] is True
    assert report["required_quote_mints"] == ["y-mint"]
    assert report["fresh_quote_map"] == {"y-mint": 0.0001}
    assert report["one_pair_evidence_tick_request_ready"] is True
    assert report["requested_position_ids"] == [
        "pair-1-incumbent",
        "pair-1-challenger",
    ]
    assert report["requires_separate_tick_authorization"] is True
    assert report["requires_fresh_recheck_before_execution"] is True
    assert report["explicit_position_scope_required"] is True
    assert report["derived_pool_safety_required_at_execution"] is True
    assert report["paper_supervisor_tick_authorized"] is False
    assert report["paper_evidence_collection_authorized"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_waits_for_chain_newer_than_entry(monkeypatch):
    temp, _, run = _build(
        monkeypatch,
        latest_chain="2026-09-30T00:40:00+00:00",
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["readiness_status"] == "WAITING_NEW_CHAIN"
    assert report["chain_newer_than_entry"] is False
    assert report["one_pair_evidence_tick_request_ready"] is False


def test_waits_for_fresh_chain_snapshot(monkeypatch):
    temp, _, run = _build(
        monkeypatch,
        latest_chain="2026-09-30T00:41:00+00:00",
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["readiness_status"] == "WAITING_CHAIN_FRESHNESS"
    assert report["chain_newer_than_entry"] is True
    assert report["chain_fresh"] is False
    assert report["one_pair_evidence_tick_request_ready"] is False


def test_waits_for_missing_quote(monkeypatch):
    temp, _, run = _build(monkeypatch, quote_time=None)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["readiness_status"] == "WAITING_QUOTES"
    assert report["quotes_ready"] is False
    assert report["one_pair_evidence_tick_request_ready"] is False


def test_external_reward_mint_also_requires_fresh_quote(monkeypatch):
    temp, _, run = _build(monkeypatch, extra_reward=True)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["readiness_status"] == "READY"
    assert report["required_quote_mints"] == [
        "reward-mint",
        "y-mint",
    ]
    assert set(report["fresh_quote_map"]) == {
        "reward-mint",
        "y-mint",
    }


def test_nonpositive_persisted_quote_fails_readiness(monkeypatch):
    temp, _, run = _build(monkeypatch, quote_value="0")
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["readiness_status"] == "WAITING_QUOTES"
    assert report["quotes_ready"] is False


def test_resealed_readiness_cannot_authorize_tick(monkeypatch):
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
        MODULE.validate_phase8_paired_ml_paper_supervision_readiness(
            report
        )


def test_readiness_does_not_mutate_database(monkeypatch):
    temp, database, run = _build(monkeypatch)
    try:
        before = _sha(database.read_bytes())
        run()
        after = _sha(database.read_bytes())
    finally:
        temp.cleanup()

    assert after == before


def test_readiness_has_no_collection_or_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "refresh_paper_chain_state(" not in source
    assert "refresh_open_paper_jupiter_quotes(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
