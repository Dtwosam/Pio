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
    / "run_phase8_paired_ml_paper_repeat_rollover_entry_once.py"
)

SPEC = importlib.util.spec_from_file_location(
    "run_phase8_paired_ml_paper_repeat_rollover_entry_once",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _hash_record(value) -> str:
    if hasattr(value, "to_record"):
        value = value.to_record()
    return _sha(MODULE._canonical_bytes(value))


class _Record:
    def __init__(self, value: dict):
        self.value = copy.deepcopy(value)

    def to_record(self):
        return copy.deepcopy(self.value)


class _Choice:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _choice(model_id: str, policy_source: str) -> dict:
    return {
        "model_id": model_id,
        "policy_source": policy_source,
        "row_index": 0,
        "pool_address": "pool-3",
        "decision_observed_at": "2026-09-30T09:10:00+00:00",
        "strategy": "SPOT",
        "half_width": 2,
        "center_offset": 0,
        "min_bin_id": 98,
        "max_bin_id": 102,
    }


def _preflight(policy_source: str) -> _Record:
    return _Record(
        {
            "policy_source": policy_source,
            "pool_address": "pool-3",
            "chain_bound": True,
        }
    )


def _seed(database: Path, *, extra_open: bool = False) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.executescript(
            """
            CREATE TABLE paper_accounts(
                account_id TEXT PRIMARY KEY,
                cash_quote REAL NOT NULL
            );
            CREATE TABLE paper_positions(
                position_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                policy_source TEXT NOT NULL,
                model_id TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_capital_quote REAL NOT NULL
            );
            CREATE TABLE paper_previews(
                position_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO paper_accounts VALUES ('paper-1', 5000.0)"
        )
        if extra_open:
            conn.execute(
                """
                INSERT INTO paper_positions VALUES(
                    'other-open', 'paper-1', 'OTHER', 'other-model',
                    'OPEN', 1.0
                )
                """
            )
        conn.commit()
    finally:
        conn.close()


def _readiness(database: Path) -> dict:
    inc_choice = _choice("champion-1", "ML_CHAMPION")
    chal_choice = _choice("challenger-1", "ML_CHALLENGER")
    return {
        "readiness_sha256": "a" * 64,
        "paper_pair_entry_execution_readiness_ready": True,
        "readiness_only": True,
        "requires_immediate_one_shot_pair_executor": True,
        "rollover_entry_request_sha256": "b" * 64,
        "fresh_signed_authorization_verification_sha256": "c" * 64,
        "rollover_entry_input_verification_sha256": "d" * 64,
        "source_final_evaluation_sha256": "e" * 64,
        "pio_database_path": str(database),
        "pio_database_sha256": _sha(database.read_bytes()),
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-2",
        "pair_id": "pair-3",
        "pool_address": "pool-3",
        "amount_x": 10,
        "amount_y": 20,
        "network_cost_y_atomic": 5,
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "required_pair_cash_quote": 2010.0,
        "max_share_bps": 500,
        "favor_x_in_active_bin": False,
        "decision_observed_at": "2026-09-30T09:10:00+00:00",
        "incumbent_position_id": "p8-pair-3-incumbent",
        "challenger_position_id": "p8-pair-3-challenger",
        "incumbent_event_key": "p8-pair-3:incumbent",
        "challenger_event_key": "p8-pair-3:challenger",
        "candidate_frame_sha256": "1" * 64,
        "incumbent_inference_sha256": "2" * 64,
        "challenger_inference_sha256": "3" * 64,
        "incumbent_choice_sha256": _hash_record(inc_choice),
        "challenger_choice_sha256": _hash_record(chal_choice),
        "incumbent_preflight_sha256": _hash_record(
            _preflight("ML_CHAMPION")
        ),
        "challenger_preflight_sha256": _hash_record(
            _preflight("ML_CHALLENGER")
        ),
        "incumbent_choice": inc_choice,
        "challenger_choice": chal_choice,
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "authorization_expires_at": "2026-09-30T10:00:00Z",
    }


def _verification() -> dict:
    return {
        "approval_payload_sha256": "4" * 64,
        "approval_signature_sha256": "5" * 64,
        "allowed_signers_sha256": "6" * 64,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _snapshot(database: Path) -> dict:
    conn = sqlite3.connect(database)
    try:
        cash = conn.execute(
            "SELECT cash_quote FROM paper_accounts WHERE account_id='paper-1'"
        ).fetchone()[0]
        positions = conn.execute(
            """
            SELECT position_id, policy_source, model_id, status,
                   entry_capital_quote
            FROM paper_positions
            ORDER BY position_id
            """
        ).fetchall()
        previews = conn.execute(
            "SELECT position_id FROM paper_previews ORDER BY position_id"
        ).fetchall()
    finally:
        conn.close()
    return {
        "cash": float(cash),
        "positions": positions,
        "previews": previews,
    }


def _build(
    monkeypatch,
    *,
    readiness_drift: bool = False,
    preflight_drift: bool = False,
    second_open_failure: bool = False,
    extra_open: bool = False,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed(database, extra_open=extra_open)
    saved = _readiness(database)

    readiness_path = _write(root / "readiness.json", saved)
    verification_path = _write(
        root / "verification.json",
        _verification(),
    )
    for name in (
        "final.json",
        "input.json",
        "account.json",
        "request.json",
        "payload.json",
    ):
        _write(root / name, {"fixture": True})
    signature = root / "signature"
    signature.write_bytes(b"sig")
    allowed = root / "allowed"
    allowed.write_bytes(b"allowed")

    calls = {"opens": [], "preflights": []}

    class FakeBaseAccount:
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

    class FakeStorage:
        def __init__(self, path):
            self.path = Path(path)

        def connect(self):
            return sqlite3.connect(self.path)

    class FakePair:
        Storage = FakeStorage
        PairedMLPaperChoice = _Choice

        @staticmethod
        def paper_account_snapshot(storage, *, account_id):
            conn = sqlite3.connect(storage.path)
            try:
                cash = conn.execute(
                    "SELECT cash_quote FROM paper_accounts WHERE account_id=?",
                    (account_id,),
                ).fetchone()[0]
                count = conn.execute(
                    """
                    SELECT COUNT(*) FROM paper_positions
                    WHERE account_id=? AND status='OPEN'
                    """,
                    (account_id,),
                ).fetchone()[0]
            finally:
                conn.close()
            return _Record(
                {
                    "cash_quote": float(cash),
                    "open_positions": int(count),
                }
            )

        @staticmethod
        def _preflight_choice(storage, *, choice, **kwargs):
            calls["preflights"].append(choice.policy_source)
            value = _preflight(choice.policy_source)
            if (
                preflight_drift
                and choice.policy_source == "ML_CHALLENGER"
            ):
                value = _Record(
                    {
                        "policy_source": "ML_CHALLENGER",
                        "pool_address": "pool-3",
                        "chain_bound": False,
                    }
                )
            return value

        @staticmethod
        def _verify_pair_transaction_state(
            conn,
            *,
            cycle_id,
            incumbent_model_id,
            challenger_model_id,
        ):
            assert cycle_id == "cycle-1"
            assert incumbent_model_id == "champion-1"
            assert challenger_model_id == "challenger-1"

        @staticmethod
        def _open_paper_position_in_conn(
            conn,
            *,
            account_id,
            position_id,
            policy_source,
            model_id,
            capital,
            cost,
            **kwargs,
        ):
            calls["opens"].append(position_id)
            if (
                second_open_failure
                and policy_source == "ML_CHALLENGER"
            ):
                raise ValueError("forced repeat second open failure")
            debit = float(capital + cost)
            conn.execute(
                """
                UPDATE paper_accounts
                SET cash_quote = cash_quote - ?
                WHERE account_id = ?
                """,
                (debit, account_id),
            )
            conn.execute(
                """
                INSERT INTO paper_positions(
                    position_id, account_id, policy_source, model_id,
                    status, entry_capital_quote
                ) VALUES (?, ?, ?, ?, 'OPEN', ?)
                """,
                (
                    position_id,
                    account_id,
                    policy_source,
                    model_id,
                    float(capital),
                ),
            )

        @staticmethod
        def _insert_counterfactual_preview(
            conn,
            *,
            position_id,
            preview,
            **kwargs,
        ):
            conn.execute(
                "INSERT INTO paper_previews VALUES (?, ?)",
                (
                    position_id,
                    json.dumps(preview.to_record(), sort_keys=True),
                ),
            )

        @staticmethod
        def paper_position_snapshot(storage, *, position_id):
            conn = sqlite3.connect(storage.path)
            try:
                row = conn.execute(
                    """
                    SELECT position_id, account_id, policy_source,
                           model_id, status, entry_capital_quote
                    FROM paper_positions
                    WHERE position_id=?
                    """,
                    (position_id,),
                ).fetchone()
            finally:
                conn.close()
            assert row is not None
            return _Record(
                {
                    "position_id": str(row[0]),
                    "account_id": str(row[1]),
                    "policy_source": str(row[2]),
                    "model_id": str(row[3]),
                    "status": str(row[4]),
                    "entry_capital_quote": float(row[5]),
                }
            )

    class FakeBaseReadiness:
        @staticmethod
        def _load_reviewed(source):
            return FakeBaseAccount, object(), object(), FakePair

    class FakeReadiness:
        @staticmethod
        def validate_phase8_paired_ml_paper_repeat_rollover_entry_execution_readiness(
            value,
        ):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_paired_ml_paper_repeat_rollover_entry_execution_readiness(
            **kwargs,
        ):
            result = copy.deepcopy(saved)
            if readiness_drift:
                result["pair_id"] = "pair-4"
            return result

        @staticmethod
        def _hash_record(value):
            return _hash_record(value)

        @staticmethod
        def _load_reviewed(source):
            return (
                object(),
                object(),
                object(),
                object(),
                FakeBaseReadiness,
            )

    monkeypatch.setattr(
        MODULE,
        "_load_readiness",
        lambda source: FakeReadiness,
    )
    monkeypatch.setattr(
        MODULE,
        "LOCK_PATH",
        root / "repeat-pair.lock",
    )

    def run():
        return MODULE.execute_phase8_paired_ml_paper_repeat_rollover_entry_once(
            repository=production,
            source_tree=ROOT,
            saved_execution_readiness_path=readiness_path,
            final_evaluation_path=root / "final.json",
            rollover_entry_input_path=root / "input.json",
            saved_account_readiness_path=root / "account.json",
            request_path=root / "request.json",
            saved_signed_authorization_verification_path=verification_path,
            signed_payload_path=root / "payload.json",
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="6" * 64,
            now="2026-09-30T09:12:00Z",
        )

    return temp, database, calls, run


def _reseal(receipt: dict) -> None:
    identity = {
        field: receipt[field]
        for field in MODULE.RECEIPT_FIELDS
    }
    receipt["receipt_sha256"] = _sha(
        MODULE._canonical_bytes(identity)
    )


def test_reviewed_readiness_is_exactly_pinned():
    path = ROOT / MODULE.READINESS_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.READINESS_TOOL
    ]


def test_rollover_one_shot_opens_exact_pair_atomically(monkeypatch):
    temp, database, calls, run = _build(monkeypatch)
    try:
        receipt = run()
        state = _snapshot(database)
    finally:
        temp.cleanup()

    assert calls["preflights"] == ["ML_CHAMPION", "ML_CHALLENGER"]
    assert calls["opens"] == [
        "p8-pair-3-incumbent",
        "p8-pair-3-challenger",
    ]
    assert state["cash"] == 2990.0
    assert len(state["positions"]) == 2
    assert len(state["previews"]) == 2
    assert receipt["previous_pair_id"] == "pair-2"
    assert receipt["pair_id"] == "pair-3"
    assert receipt["rollover_entry_request_sha256"] == "b" * 64
    assert receipt["rollover_entry_input_verification_sha256"] == "d" * 64
    assert receipt["account_open_positions_before"] == 0
    assert receipt["account_open_positions_after"] == 2
    assert receipt["zero_open_positions_verified_at_commit"] is True
    assert receipt["counterfactual_preflights_reverified"] is True
    assert receipt["atomic_pair_open_verified"] is True
    assert receipt["paper_pair_entry_executed"] is True
    assert receipt["paper_evidence_collection_authorized"] is False
    assert receipt["paper_trading_authorized"] is False
    assert receipt["live_submit_authorized"] is False


def test_fresh_readiness_drift_fails_before_mutation(monkeypatch):
    temp, database, calls, run = _build(
        monkeypatch,
        readiness_drift=True,
    )
    try:
        before = _snapshot(database)
        with pytest.raises(ValueError, match="differs from saved"):
            run()
        after = _snapshot(database)
    finally:
        temp.cleanup()

    assert calls["opens"] == []
    assert after == before


def test_preflight_drift_fails_before_mutation(monkeypatch):
    temp, database, calls, run = _build(
        monkeypatch,
        preflight_drift=True,
    )
    try:
        before = _snapshot(database)
        with pytest.raises(ValueError, match="challenger preflight drifted"):
            run()
        after = _snapshot(database)
    finally:
        temp.cleanup()

    assert calls["opens"] == []
    assert after == before


def test_existing_open_position_blocks_rollover_pair(monkeypatch):
    temp, database, calls, run = _build(
        monkeypatch,
        extra_open=True,
    )
    try:
        before = _snapshot(database)
        with pytest.raises(ValueError, match="zero open positions"):
            run()
        after = _snapshot(database)
    finally:
        temp.cleanup()

    assert calls["opens"] == []
    assert after == before


def test_second_open_failure_rolls_back_entire_rollover_pair(monkeypatch):
    temp, database, calls, run = _build(
        monkeypatch,
        second_open_failure=True,
    )
    try:
        before = _snapshot(database)
        with pytest.raises(
            ValueError,
            match="forced repeat second open failure",
        ):
            run()
        after = _snapshot(database)
    finally:
        temp.cleanup()

    assert calls["opens"] == [
        "p8-pair-3-incumbent",
        "p8-pair-3-challenger",
    ]
    assert after == before


def test_resealed_receipt_cannot_authorize_ongoing_paper(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["paper_trading_authorized"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_repeat_rollover_entry_execution_receipt(
            receipt
        )


def test_resealed_receipt_cannot_authorize_promotion(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["continuous_promotion_authorized"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="continuous_promotion_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_repeat_rollover_entry_execution_receipt(
            receipt
        )


def test_resealed_receipt_cannot_reuse_previous_pair(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["pair_id"] = receipt["previous_pair_id"]
    _reseal(receipt)
    with pytest.raises(ValueError, match="pair id was not advanced"):
        MODULE.validate_phase8_paired_ml_paper_repeat_rollover_entry_execution_receipt(
            receipt
        )


def test_rollover_executor_has_no_scheduler_or_live_submit_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "promote_continuous_challenger(" not in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
