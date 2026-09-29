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
    / "run_phase8_paper_challenger_transition_once.py"
)

SPEC = importlib.util.spec_from_file_location(
    "run_phase8_paper_challenger_transition_once",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


MODEL_ID = "challenger-1"
CYCLE_ID = "cycle-1"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _seed_database(
    path: Path,
    *,
    cycle_model: str = MODEL_ID,
    other_paper: bool = False,
) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE model_registry(
                model_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                model_family TEXT NOT NULL,
                feature_version TEXT NOT NULL,
                dataset_version TEXT NOT NULL
            );
            CREATE TABLE model_offline_evidence(
                model_id TEXT PRIMARY KEY,
                evidence_type TEXT NOT NULL,
                qualified INTEGER NOT NULL
            );
            CREATE TABLE continuous_learning_cycles(
                cycle_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                active_key TEXT,
                challenger_model_id TEXT,
                updated_at TEXT NOT NULL,
                champion_model_id TEXT NOT NULL,
                target_dataset_version TEXT NOT NULL
            );
            CREATE TABLE phase8_model_status_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                old_status TEXT,
                new_status TEXT NOT NULL
            );
            CREATE TABLE phase8_cycle_status_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                old_status TEXT,
                new_status TEXT NOT NULL,
                old_challenger_model_id TEXT,
                new_challenger_model_id TEXT,
                old_active_key TEXT,
                new_active_key TEXT
            );
            CREATE TRIGGER model_history
            AFTER UPDATE OF status ON model_registry
            WHEN OLD.status <> NEW.status
            BEGIN
                INSERT INTO phase8_model_status_history(
                    model_id, changed_at, old_status, new_status
                ) VALUES (
                    NEW.model_id, NEW.updated_at, OLD.status, NEW.status
                );
            END;
            CREATE TRIGGER cycle_history
            AFTER UPDATE OF status, challenger_model_id, active_key
            ON continuous_learning_cycles
            WHEN (
                OLD.status <> NEW.status
                OR IFNULL(OLD.challenger_model_id, '')
                   <> IFNULL(NEW.challenger_model_id, '')
                OR IFNULL(OLD.active_key, '')
                   <> IFNULL(NEW.active_key, '')
            )
            BEGIN
                INSERT INTO phase8_cycle_status_history(
                    cycle_id, changed_at, old_status, new_status,
                    old_challenger_model_id, new_challenger_model_id,
                    old_active_key, new_active_key
                ) VALUES (
                    NEW.cycle_id, NEW.updated_at,
                    OLD.status, NEW.status,
                    OLD.challenger_model_id, NEW.challenger_model_id,
                    OLD.active_key, NEW.active_key
                );
            END;
            """
        )
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, status, updated_at,
                model_family, feature_version, dataset_version
            ) VALUES (?, 'OFFLINE_QUALIFIED', 'before', 'TEST', 'V1', 'D1')
            """,
            (MODEL_ID,),
        )
        conn.execute(
            """
            INSERT INTO model_offline_evidence(
                model_id, evidence_type, qualified
            ) VALUES (?, 'OFFLINE_CHALLENGER_V1', 1)
            """,
            (MODEL_ID,),
        )
        conn.execute(
            """
            INSERT INTO continuous_learning_cycles(
                cycle_id, status, active_key, challenger_model_id,
                updated_at, champion_model_id, target_dataset_version
            ) VALUES (
                ?, 'OFFLINE_QUALIFIED', 'ACTIVE', ?,
                'before', 'champion-1', 'D2'
            )
            """,
            (CYCLE_ID, cycle_model),
        )
        conn.execute(
            """
            INSERT INTO phase8_model_status_history(
                model_id, changed_at, old_status, new_status
            ) VALUES (?, 'before', 'OFFLINE_CANDIDATE', 'OFFLINE_QUALIFIED')
            """,
            (MODEL_ID,),
        )
        conn.execute(
            """
            INSERT INTO phase8_cycle_status_history(
                cycle_id, changed_at, old_status, new_status,
                old_challenger_model_id, new_challenger_model_id,
                old_active_key, new_active_key
            ) VALUES (
                ?, 'before', 'CHALLENGER_REGISTERED', 'OFFLINE_QUALIFIED',
                ?, ?, 'ACTIVE', 'ACTIVE'
            )
            """,
            (CYCLE_ID, cycle_model, cycle_model),
        )
        if other_paper:
            conn.execute(
                """
                INSERT INTO model_registry(
                    model_id, status, updated_at,
                    model_family, feature_version, dataset_version
                ) VALUES (
                    'other-paper', 'PAPER_CHALLENGER', 'before',
                    'TEST', 'V1', 'D0'
                )
                """
            )
        conn.commit()
    finally:
        conn.close()


def _readiness(production: Path, database: Path) -> dict:
    return {
        "readiness_sha256": "a" * 64,
        "fresh_post_audit_sha256": "b" * 64,
        "paper_transition_execution_readiness_ready": True,
        "requires_immediate_one_shot_paper_transition_executor": True,
        "readiness_only": True,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": MODULE._database_state(database)["database"],
        "pio_wal_sha256": MODULE._database_state(database)["wal"],
        "pio_shm_sha256": MODULE._database_state(database)["shm"],
        "research_artifacts_sha256": _sha(b"[]"),
        "research_artifact_count": 0,
        "model_id": MODEL_ID,
        "active_cycle_id": CYCLE_ID,
        "transition_request_sha256": "c" * 64,
        "fresh_signed_authorization_verification_sha256": "d" * 64,
        "approval_payload_sha256": "e" * 64,
        "approval_signature_sha256": "f" * 64,
        "allowed_signers_sha256": "1" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "authorization_expires_at": "2026-09-29T22:00:00Z",
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed_database(database)

    saved = _readiness(production, database)
    fresh = copy.deepcopy(saved)
    fresh["readiness_sha256"] = "2" * 64
    fresh["fresh_post_audit_sha256"] = "3" * 64
    saved_path = _write(root / "readiness.json", saved)

    class FakeReadiness:
        @staticmethod
        def validate_phase8_paper_challenger_transition_execution_readiness(
            value,
        ):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_paper_challenger_transition_execution_readiness(
            **kwargs,
        ):
            return copy.deepcopy(fresh)

    monkeypatch.setattr(
        MODULE,
        "_load_readiness_module",
        lambda source: FakeReadiness,
    )
    monkeypatch.setattr(
        MODULE,
        "_research_artifacts",
        lambda source, readiness_module, data_root: [],
    )
    monkeypatch.setattr(
        MODULE,
        "LOCK_PATH",
        root / "transition.lock",
    )

    def run():
        return MODULE.execute_phase8_paper_challenger_transition_once(
            repository=production,
            source_tree=ROOT,
            saved_readiness_path=saved_path,
            saved_post_audit_path=root / "audit.json",
            execution_receipt_path=root / "offline-receipt.json",
            transition_request_path=root / "request.json",
            saved_signed_authorization_verification_path=(
                root / "verification.json"
            ),
            signed_payload_path=root / "payload.json",
            signature_path=root / "signature",
            allowed_signers_path=root / "allowed",
            expected_allowed_signers_sha256="1" * 64,
            now="2026-09-29T21:55:00Z",
        )

    return temp, database, run


def _reseal(receipt: dict) -> None:
    identity = {field: receipt[field] for field in MODULE.RECEIPT_FIELDS}
    receipt["receipt_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_atomic_transition_updates_model_cycle_and_histories(tmp_path):
    database = tmp_path / "pio.db"
    _seed_database(database)

    result = MODULE._atomic_transition(
        database,
        model_id=MODEL_ID,
        cycle_id=CYCLE_ID,
    )

    assert result["model_status_before"] == "OFFLINE_QUALIFIED"
    assert result["model_status_after"] == "PAPER_CHALLENGER"
    assert result["cycle_status_before"] == "OFFLINE_QUALIFIED"
    assert result["cycle_status_after"] == "PAPER_CHALLENGER"
    assert result["offline_evidence_qualified"] is True
    assert result["model_history_count_after"] == (
        result["model_history_count_before"] + 1
    )
    assert result["cycle_history_count_after"] == (
        result["cycle_history_count_before"] + 1
    )
    assert result["model_history_latest"]["old_status"] == "OFFLINE_QUALIFIED"
    assert result["model_history_latest"]["new_status"] == "PAPER_CHALLENGER"
    assert result["cycle_history_latest"]["old_status"] == "OFFLINE_QUALIFIED"
    assert result["cycle_history_latest"]["new_status"] == "PAPER_CHALLENGER"

    conn = sqlite3.connect(database)
    try:
        model = conn.execute(
            "SELECT status FROM model_registry WHERE model_id = ?",
            (MODEL_ID,),
        ).fetchone()
        cycle = conn.execute(
            """
            SELECT status, active_key, challenger_model_id
            FROM continuous_learning_cycles
            WHERE cycle_id = ?
            """,
            (CYCLE_ID,),
        ).fetchone()
    finally:
        conn.close()

    assert model == ("PAPER_CHALLENGER",)
    assert cycle == ("PAPER_CHALLENGER", "ACTIVE", MODEL_ID)


def test_atomic_transition_rolls_back_if_cycle_update_fails_after_model_update(
    tmp_path,
):
    database = tmp_path / "pio.db"
    _seed_database(database)

    conn = sqlite3.connect(database)
    try:
        conn.execute(
            """
            CREATE TRIGGER refuse_cycle_paper_transition
            BEFORE UPDATE OF status ON continuous_learning_cycles
            WHEN NEW.status = 'PAPER_CHALLENGER'
            BEGIN
                SELECT RAISE(ABORT, 'cycle sync refused');
            END;
            """
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="cycle sync refused"):
        MODULE._atomic_transition(
            database,
            model_id=MODEL_ID,
            cycle_id=CYCLE_ID,
        )

    conn = sqlite3.connect(database)
    try:
        model = conn.execute(
            "SELECT status FROM model_registry WHERE model_id = ?",
            (MODEL_ID,),
        ).fetchone()
        cycle = conn.execute(
            "SELECT status FROM continuous_learning_cycles WHERE cycle_id = ?",
            (CYCLE_ID,),
        ).fetchone()
        model_history = conn.execute(
            """
            SELECT COUNT(*)
            FROM phase8_model_status_history
            WHERE model_id = ?
            """,
            (MODEL_ID,),
        ).fetchone()
        cycle_history = conn.execute(
            """
            SELECT COUNT(*)
            FROM phase8_cycle_status_history
            WHERE cycle_id = ?
            """,
            (CYCLE_ID,),
        ).fetchone()
    finally:
        conn.close()

    assert model == ("OFFLINE_QUALIFIED",)
    assert cycle == ("OFFLINE_QUALIFIED",)
    assert model_history == (1,)
    assert cycle_history == (1,)


def test_atomic_transition_refuses_unqualified_offline_evidence(tmp_path):
    database = tmp_path / "pio.db"
    _seed_database(database)
    conn = sqlite3.connect(database)
    try:
        conn.execute(
            """
            UPDATE model_offline_evidence
            SET qualified = 0
            WHERE model_id = ?
            """,
            (MODEL_ID,),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError, match="evidence is not qualified"):
        MODULE._atomic_transition(
            database,
            model_id=MODEL_ID,
            cycle_id=CYCLE_ID,
        )

    conn = sqlite3.connect(database)
    try:
        status = conn.execute(
            "SELECT status FROM model_registry WHERE model_id = ?",
            (MODEL_ID,),
        ).fetchone()
    finally:
        conn.close()
    assert status == ("OFFLINE_QUALIFIED",)


def test_atomic_transition_rolls_back_on_cycle_binding_drift(tmp_path):
    database = tmp_path / "pio.db"
    _seed_database(database, cycle_model="challenger-2")

    with pytest.raises(ValueError, match="cycle/model binding"):
        MODULE._atomic_transition(
            database,
            model_id=MODEL_ID,
            cycle_id=CYCLE_ID,
        )

    conn = sqlite3.connect(database)
    try:
        model = conn.execute(
            "SELECT status FROM model_registry WHERE model_id = ?",
            (MODEL_ID,),
        ).fetchone()
        cycle = conn.execute(
            "SELECT status FROM continuous_learning_cycles WHERE cycle_id = ?",
            (CYCLE_ID,),
        ).fetchone()
    finally:
        conn.close()

    assert model == ("OFFLINE_QUALIFIED",)
    assert cycle == ("OFFLINE_QUALIFIED",)


def test_atomic_transition_refuses_parallel_paper_challenger(tmp_path):
    database = tmp_path / "pio.db"
    _seed_database(database, other_paper=True)

    with pytest.raises(ValueError, match="already exists"):
        MODULE._atomic_transition(
            database,
            model_id=MODEL_ID,
            cycle_id=CYCLE_ID,
        )

    conn = sqlite3.connect(database)
    try:
        status = conn.execute(
            "SELECT status FROM model_registry WHERE model_id = ?",
            (MODEL_ID,),
        ).fetchone()
    finally:
        conn.close()
    assert status == ("OFFLINE_QUALIFIED",)


def test_one_shot_executor_transitions_state_without_starting_paper_trades(
    monkeypatch,
):
    temp, database, run = _build(monkeypatch)
    try:
        receipt = run()

        conn = sqlite3.connect(database)
        try:
            model = conn.execute(
                "SELECT status FROM model_registry WHERE model_id = ?",
                (MODEL_ID,),
            ).fetchone()
            cycle = conn.execute(
                "SELECT status FROM continuous_learning_cycles WHERE cycle_id = ?",
                (CYCLE_ID,),
            ).fetchone()
        finally:
            conn.close()
    finally:
        temp.cleanup()

    assert model == ("PAPER_CHALLENGER",)
    assert cycle == ("PAPER_CHALLENGER",)
    assert receipt["fresh_readiness_matches_saved"] is True
    assert receipt[
        "human_paper_challenger_transition_authorization_verified"
    ] is True
    assert receipt["paper_challenger_transition_authorized"] is True
    assert receipt["paper_challenger_transition_executed"] is True
    assert receipt["model_transition_completed"] is True
    assert receipt["cycle_sync_completed"] is True
    assert receipt["transition_completed"] is True
    assert receipt["requires_post_transition_audit"] is True
    assert receipt["paper_evidence_collection_authorized"] is False
    assert receipt["paper_trading_authorized"] is False
    assert receipt["live_submit_authorized"] is False
    assert receipt["new_live_capital_used"] is False
    assert receipt["phase8_execution_authorized"] is False
    assert receipt["phase8_promotion_authorized"] is False
    assert receipt["production_pio_database_modified"] is True
    assert receipt["production_research_artifacts_modified"] is False


def test_substantive_fresh_readiness_drift_fails_before_transition(
    monkeypatch,
):
    temp, database, _ = _build(monkeypatch)
    try:
        saved = _readiness(
            Path(database).parents[1],
            database,
        )
        fresh = copy.deepcopy(saved)
        fresh["readiness_sha256"] = "2" * 64
        fresh["fresh_post_audit_sha256"] = "3" * 64
        fresh["model_id"] = "challenger-2"

        class DriftedReadiness:
            @staticmethod
            def validate_phase8_paper_challenger_transition_execution_readiness(
                value,
            ):
                assert isinstance(value, dict)

            @staticmethod
            def build_phase8_paper_challenger_transition_execution_readiness(
                **kwargs,
            ):
                return copy.deepcopy(fresh)

        monkeypatch.setattr(
            MODULE,
            "_load_readiness_module",
            lambda source: DriftedReadiness,
        )

        root = Path(temp.name)
        saved_path = root / "readiness.json"
        before = sqlite3.connect(database)
        try:
            status_before = before.execute(
                "SELECT status FROM model_registry WHERE model_id = ?",
                (MODEL_ID,),
            ).fetchone()
        finally:
            before.close()

        with pytest.raises(ValueError, match="stable state differs"):
            MODULE.execute_phase8_paper_challenger_transition_once(
                repository=Path(database).parents[1],
                source_tree=ROOT,
                saved_readiness_path=saved_path,
                saved_post_audit_path=root / "audit.json",
                execution_receipt_path=root / "offline-receipt.json",
                transition_request_path=root / "request.json",
                saved_signed_authorization_verification_path=(
                    root / "verification.json"
                ),
                signed_payload_path=root / "payload.json",
                signature_path=root / "signature",
                allowed_signers_path=root / "allowed",
                expected_allowed_signers_sha256="1" * 64,
                now="2026-09-29T21:55:00Z",
            )

        after = sqlite3.connect(database)
        try:
            status_after = after.execute(
                "SELECT status FROM model_registry WHERE model_id = ?",
                (MODEL_ID,),
            ).fetchone()
        finally:
            after.close()
    finally:
        temp.cleanup()

    assert status_before == ("OFFLINE_QUALIFIED",)
    assert status_after == ("OFFLINE_QUALIFIED",)


def test_resealed_receipt_cannot_authorize_paper_evidence_collection(
    monkeypatch,
):
    temp, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["paper_evidence_collection_authorized"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="paper_evidence_collection_authorized=false",
    ):
        MODULE.validate_phase8_paper_challenger_transition_execution_receipt(
            receipt
        )


def test_resealed_receipt_cannot_authorize_live_submit(monkeypatch):
    temp, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["live_submit_authorized"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="live_submit_authorized=false",
    ):
        MODULE.validate_phase8_paper_challenger_transition_execution_receipt(
            receipt
        )


def test_executor_has_no_paper_trade_or_live_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "paper_scheduler" not in source
    assert "paper_trade" not in source
    assert "run_manual_market_paper" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"new_live_capital_used": False' in source
