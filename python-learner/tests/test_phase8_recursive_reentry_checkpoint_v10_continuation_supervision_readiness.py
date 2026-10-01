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
    / "check_phase8_recursive_reentry_checkpoint_v10_continuation_supervision_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_recursive_reentry_checkpoint_v10_continuation_supervision_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _seed_database(
    path: Path,
    *,
    latest_chain: str,
    quote_at: str,
    cycle_status: str = "PAPER_CHALLENGER",
) -> None:
    conn = sqlite3.connect(path)
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
        for position_id in (
            "p8-pair-4-incumbent",
            "p8-pair-4-challenger",
        ):
            conn.execute(
                """
                INSERT INTO paper_positions(
                    position_id, status, pool_address
                ) VALUES (?, 'OPEN', 'pool-4')
                """,
                (position_id,),
            )
            conn.execute(
                """
                INSERT INTO paper_counterfactual_positions(
                    position_id, token_x_mint, token_y_mint,
                    reward_mint_0, reward_mint_1
                ) VALUES (?, 'x-mint', 'y-mint', NULL, NULL)
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
        conn.execute(
            """
            INSERT INTO token_quote_observations(
                token_mint, quote_unit, quote_per_atomic,
                source, observed_at
            ) VALUES (
                'y-mint', 'USD', '0.000001', 'fixture', ?
            )
            """,
            (quote_at,),
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
                'cycle-1', ?, 'ACTIVE',
                'champion-1', 'challenger-1'
            )
            """,
            (cycle_status,),
        )
        conn.commit()
    finally:
        conn.close()


def _checkpoint(database: Path) -> dict:
    return {
        "checkpoint_sha256": "a" * 64,
        "source_post_audit_sha256": "b" * 64,
        "source_execution_receipt_sha256": "c" * 64,
        "source_final_evaluation_sha256": "d" * 64,
        "source_pair_entry_post_audit_sha256": "e" * 64,
        "pair_entry_request_sha256": "f" * 64,
        "pair_entry_input_verification_sha256": "1" * 64,
        "pair_lineage_sha256": "2" * 64,
        "latest_previous_tick_post_audit_sha256": "3" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": str(database),
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-3",
        "pair_id": "pair-4",
        "pool_address": "pool-4",
        "entry_observed_at": "2026-09-30T10:20:00+00:00",
        "incumbent_position_id": "p8-pair-4-incumbent",
        "challenger_position_id": "p8-pair-4-challenger",
        "requested_position_ids": [
            "p8-pair-4-incumbent",
            "p8-pair-4-challenger",
        ],
        "latest_tick_observed_at": "2026-09-30T10:25:00+00:00",
        "checkpoint_state": "CONTINUE",
        "continuation_review_ready": True,
        "terminal_review_ready": False,
        "recovery_review_ready": False,
        "pair_both_open": True,
        "pair_any_closed": False,
    }

def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    latest_chain: str = "2026-09-30T10:30:00+00:00",
    quote_at: str = "2026-09-30T10:30:00+00:00",
    cycle_status: str = "PAPER_CHALLENGER",
    checkpoint_mutator=None,
    as_of: str = "2026-09-30T10:31:00+00:00",
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed_database(
        database,
        latest_chain=latest_chain,
        quote_at=quote_at,
        cycle_status=cycle_status,
    )
    checkpoint = _checkpoint(database)
    if checkpoint_mutator:
        checkpoint_mutator(checkpoint)
    checkpoint_path = _write(root / "checkpoint.json", checkpoint)

    class FakeCheckpointV2:
        STATE_CONTINUE = "CONTINUE"
        STATE_TERMINAL = "TERMINAL"
        STATE_RECOVERY = "RECOVERY"

        @staticmethod
        def validate_phase8_recursive_reentry_continuation_checkpoint_v10(value):
            assert isinstance(value, dict)

    monkeypatch.setattr(
        MODULE,
        "_load_checkpoint",
        lambda source: FakeCheckpointV2,
    )

    def run():
        return MODULE.build_phase8_recursive_reentry_checkpoint_v10_continuation_supervision_readiness(
            repository=production,
            source_tree=ROOT,
            checkpoint_path=checkpoint_path,
            as_of=as_of,
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


def test_reviewed_checkpoint_v10_is_exactly_pinned():
    path = ROOT / MODULE.CHECKPOINT_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.CHECKPOINT_TOOL
    ]


def test_new_chain_and_fresh_quotes_make_continuation_ready(
    monkeypatch,
):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["previous_pair_id"] == "pair-3"
    assert report["pair_id"] == "pair-4"
    assert report["source_checkpoint_sha256"] == "a" * 64
    assert report["source_post_audit_sha256"] == "b" * 64
    assert report["source_execution_receipt_sha256"] == "c" * 64
    assert report["source_final_evaluation_sha256"] == "d" * 64
    assert report["source_pair_entry_post_audit_sha256"] == "e" * 64
    assert report["pair_entry_request_sha256"] == "f" * 64
    assert report["pair_entry_input_verification_sha256"] == "1" * 64
    assert report["pair_lineage_sha256"] == "2" * 64
    assert report["latest_previous_tick_post_audit_sha256"] == "3" * 64
    assert report["previous_tick_observed_at"] == (
        "2026-09-30T10:25:00+00:00"
    )
    assert report["latest_chain_observed_at"] == (
        "2026-09-30T10:30:00+00:00"
    )
    assert report["chain_newer_than_previous_tick"] is True
    assert report["chain_fresh"] is True
    assert report["quotes_ready"] is True
    assert report["cycle_model_binding_valid"] is True
    assert report["readiness_status"] == "READY"
    assert report["continuation_tick_request_ready"] is True
    assert report["requires_separate_tick_authorization"] is True
    assert report["paper_supervisor_tick_authorized"] is False
    assert report["paper_evidence_collection_authorized"] is False
    assert report["continuous_promotion_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_same_chain_snapshot_waits_for_new_chain(monkeypatch):
    temp, run = _build(
        monkeypatch,
        latest_chain="2026-09-30T10:25:00+00:00",
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["chain_newer_than_previous_tick"] is False
    assert report["readiness_status"] == "WAITING_NEW_CHAIN"
    assert report["continuation_tick_request_ready"] is False


def test_stale_quote_waits_for_quotes(monkeypatch):
    temp, run = _build(
        monkeypatch,
        quote_at="2026-09-30T10:10:00+00:00",
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["chain_newer_than_previous_tick"] is True
    assert report["chain_fresh"] is True
    assert report["quotes_ready"] is False
    assert report["readiness_status"] == "WAITING_QUOTES"
    assert report["continuation_tick_request_ready"] is False


def test_cycle_model_drift_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        cycle_status="COMPLETED",
    )
    try:
        with pytest.raises(ValueError, match="cycle/model binding changed"):
            run()
    finally:
        temp.cleanup()


def test_recovery_route_cannot_enter_continuation(monkeypatch):
    temp, run = _build(
        monkeypatch,
        checkpoint_mutator=lambda value: value.update(
            checkpoint_state="RECOVERY",
            continuation_review_ready=False,
            recovery_review_ready=True,
        ),
    )
    try:
        with pytest.raises(
            ValueError,
            match="does not route to continuation",
        ):
            run()
    finally:
        temp.cleanup()


def test_terminal_route_cannot_enter_continuation(monkeypatch):
    temp, run = _build(
        monkeypatch,
        checkpoint_mutator=lambda value: value.update(
            checkpoint_state="TERMINAL",
            continuation_review_ready=False,
            terminal_review_ready=True,
            pair_both_open=False,
            pair_any_closed=True,
        ),
    )
    try:
        with pytest.raises(
            ValueError,
            match="does not route to continuation",
        ):
            run()
    finally:
        temp.cleanup()


def test_checkpoint_without_continuation_review_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        checkpoint_mutator=lambda value: value.update(
            continuation_review_ready=False,
        ),
    )
    try:
        with pytest.raises(ValueError, match="not continuation-ready"):
            run()
    finally:
        temp.cleanup()


def test_resealed_readiness_cannot_authorize_tick(monkeypatch):
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
        MODULE.validate_phase8_recursive_reentry_checkpoint_v10_continuation_supervision_readiness(
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
        MODULE.validate_phase8_recursive_reentry_checkpoint_v10_continuation_supervision_readiness(
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
    with pytest.raises(
        ValueError,
        match="live_submit_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v10_continuation_supervision_readiness(
            report
        )


def test_checkpoint_continuation_readiness_has_no_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
