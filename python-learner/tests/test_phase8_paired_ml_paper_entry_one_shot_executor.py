from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "run_phase8_paired_ml_paper_entry_once.py"
)

SPEC = importlib.util.spec_from_file_location(
    "run_phase8_paired_ml_paper_entry_once",
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
    return hashlib.sha256(
        MODULE._canonical_bytes(value)
    ).hexdigest()


class _Record:
    def __init__(self, value: dict):
        self.value = copy.deepcopy(value)

    def to_record(self) -> dict:
        return copy.deepcopy(self.value)


def _choice(model_id: str, policy_source: str) -> dict:
    return {
        "model_id": model_id,
        "policy_source": policy_source,
        "row_index": 0,
        "pool_address": "pool-1",
        "decision_observed_at": "2026-09-30T00:40:00+00:00",
        "strategy": "SPOT",
        "half_width": 2,
        "center_offset": 0,
        "min_bin_id": 98,
        "max_bin_id": 102,
        "risk_adjusted_score_bps": 10.0,
        "predicted_positive_excess_probability": 0.7,
        "predicted_range_survival": 0.8,
    }


def _preflight(policy_source: str) -> dict:
    return {
        "policy_source": policy_source,
        "pool_address": "pool-1",
        "entry_observed_at": "2026-09-30T00:40:00+00:00",
        "strategy": "SPOT",
        "min_bin_id": 98,
        "max_bin_id": 102,
        "chain_bound": True,
    }


def _seed_database(path: Path) -> None:
    conn = sqlite3.connect(path)
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
            "INSERT INTO paper_accounts(account_id, cash_quote) VALUES (?, ?)",
            ("paper-1", 5000.0),
        )
        conn.commit()
    finally:
        conn.close()


def _readiness(production: Path, database: Path) -> dict:
    incumbent_choice = _choice("champion-1", "ML_CHAMPION")
    challenger_choice = _choice("challenger-1", "ML_CHALLENGER")
    incumbent_preflight = _Record(_preflight("ML_CHAMPION"))
    challenger_preflight = _Record(_preflight("ML_CHALLENGER"))
    return {
        "readiness_sha256": "a" * 64,
        "paper_pair_entry_execution_readiness_ready": True,
        "readiness_only": True,
        "requires_immediate_one_shot_pair_executor": True,
        "pio_database_path": str(database),
        "pio_database_sha256": _sha(database.read_bytes()),
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "paired_entry_request_sha256": "b" * 64,
        "fresh_signed_authorization_verification_sha256": "c" * 64,
        "paired_entry_input_verification_sha256": "d" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "authorization_expires_at": "2026-09-30T01:00:00Z",
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "pair_id": "pair-1",
        "pool_address": "pool-1",
        "amount_x": 10,
        "amount_y": 20,
        "network_cost_y_atomic": 5,
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "required_pair_cash_quote": 2010.0,
        "as_of": None,
        "lookback_observations": 12,
        "half_widths": [0, 1, 2, 5, 10],
        "center_offsets": [0],
        "strategies": ["SPOT", "CURVE", "BID_ASK"],
        "max_share_bps": 500,
        "favor_x_in_active_bin": False,
        "near_liquidity_radius": 5,
        "risk_lambda": 1.5,
        "min_positive_excess_probability": 0.55,
        "min_range_survival_probability": 0.5,
        "min_score_bps": 0.0,
        "decision_observed_at": "2026-09-30T00:40:00+00:00",
        "incumbent_position_id": "pair-1-incumbent",
        "challenger_position_id": "pair-1-challenger",
        "incumbent_event_key": "pair-1-incumbent-enter",
        "challenger_event_key": "pair-1-challenger-enter",
        "candidate_frame_sha256": "1" * 64,
        "incumbent_inference_sha256": "2" * 64,
        "challenger_inference_sha256": "3" * 64,
        "incumbent_choice_sha256": _hash_record(incumbent_choice),
        "challenger_choice_sha256": _hash_record(challenger_choice),
        "incumbent_preflight_sha256": _hash_record(incumbent_preflight),
        "challenger_preflight_sha256": _hash_record(challenger_preflight),
        "incumbent_choice": incumbent_choice,
        "challenger_choice": challenger_choice,
    }


def _account_readiness() -> dict:
    return {
        "account_cash_quote": 5000.0,
        "account_open_positions": 0,
    }


def _signed_verification() -> dict:
    return {
        "approval_payload_sha256": "e" * 64,
        "approval_signature_sha256": "f" * 64,
        "allowed_signers_sha256": "4" * 64,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    fresh_readiness_mutator=None,
    preflight_drift: bool = False,
    fail_second_open: bool = False,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed_database(database)

    readiness = _readiness(production, database)
    fresh = copy.deepcopy(readiness)
    if fresh_readiness_mutator:
        fresh_readiness_mutator(fresh)

    saved_readiness_path = _write(root / "readiness.json", readiness)
    saved_account_path = _write(
        root / "account-readiness.json",
        _account_readiness(),
    )
    signed_verification_path = _write(
        root / "verification.json",
        _signed_verification(),
    )
    for name in (
        "post-audit.json",
        "paper-evidence-input.json",
        "pair-input.json",
        "request.json",
        "payload.json",
    ):
        _write(root / name, {"fixture": True})
    signature = root / "signature"
    signature.write_bytes(b"sig")
    allowed = root / "allowed"
    allowed.write_bytes(b"allowed")

    calls = {"opens": [], "preflights": []}

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
        def validate_phase8_paired_ml_paper_entry_execution_readiness(
            value,
        ):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_paired_ml_paper_entry_execution_readiness(
            **kwargs,
        ):
            return copy.deepcopy(fresh)

        @staticmethod
        def _hash_record(value):
            return _hash_record(value)

        @staticmethod
        def _load_reviewed(source):
            return FakeAccount, object(), object(), FakePair

    class FakeChoice:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class FakeStorage:
        def __init__(self, path):
            self.path = Path(path)

        def connect(self):
            return sqlite3.connect(self.path)

    class FakePair:
        PairedMLPaperChoice = FakeChoice
        Storage = FakeStorage

        @staticmethod
        def _preflight_choice(storage, *, choice, **kwargs):
            calls["preflights"].append(choice.policy_source)
            record = _preflight(choice.policy_source)
            if preflight_drift and choice.policy_source == "ML_CHALLENGER":
                record["max_bin_id"] = 105
            return _Record(record)

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
                fail_second_open
                and policy_source == "ML_CHALLENGER"
            ):
                raise ValueError("forced second pair open failure")
            cash = conn.execute(
                """
                SELECT cash_quote
                FROM paper_accounts
                WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
            assert cash is not None
            debit = float(capital + cost)
            if float(cash[0]) < debit:
                raise ValueError("insufficient paper cash")
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
                    position_id, account_id, policy_source,
                    model_id, status, entry_capital_quote
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
                """
                INSERT INTO paper_previews(position_id, payload)
                VALUES (?, ?)
                """,
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
                    WHERE position_id = ?
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

        @staticmethod
        def paper_account_snapshot(storage, *, account_id):
            conn = sqlite3.connect(storage.path)
            try:
                cash = conn.execute(
                    """
                    SELECT cash_quote
                    FROM paper_accounts
                    WHERE account_id = ?
                    """,
                    (account_id,),
                ).fetchone()
                count = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM paper_positions
                    WHERE account_id = ? AND status = 'OPEN'
                    """,
                    (account_id,),
                ).fetchone()
            finally:
                conn.close()
            assert cash is not None and count is not None
            return _Record(
                {
                    "cash_quote": float(cash[0]),
                    "open_positions": int(count[0]),
                }
            )

    monkeypatch.setattr(
        MODULE,
        "_load_readiness_module",
        lambda source: FakeReadiness,
    )
    monkeypatch.setattr(
        MODULE,
        "LOCK_PATH",
        root / "paired-paper-one-shot.lock",
    )

    def run():
        return MODULE.execute_phase8_paired_ml_paper_entry_once(
            repository=production,
            source_tree=ROOT,
            saved_readiness_path=saved_readiness_path,
            saved_account_readiness_path=saved_account_path,
            post_audit_path=root / "post-audit.json",
            paper_evidence_input_path=root / "paper-evidence-input.json",
            paired_entry_input_path=root / "pair-input.json",
            request_path=root / "request.json",
            saved_signed_authorization_verification_path=(
                signed_verification_path
            ),
            signed_payload_path=root / "payload.json",
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="4" * 64,
            now="2026-09-30T00:45:00Z",
        )

    return temp, database, calls, run


def _database_snapshot(database: Path) -> dict:
    conn = sqlite3.connect(database)
    try:
        cash = conn.execute(
            "SELECT cash_quote FROM paper_accounts WHERE account_id='paper-1'"
        ).fetchone()
        positions = conn.execute(
            """
            SELECT position_id, policy_source, model_id, entry_capital_quote
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
        "cash": float(cash[0]),
        "positions": positions,
        "previews": previews,
    }


def _reseal(receipt: dict) -> None:
    identity = {
        field: receipt[field]
        for field in MODULE.RECEIPT_FIELDS
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_readiness_is_exactly_pinned():
    path = ROOT / MODULE.READINESS_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.READINESS_TOOL
    ]


def test_one_shot_opens_exact_readiness_bound_pair(monkeypatch):
    temp, database, calls, run = _build(monkeypatch)
    try:
        receipt = run()
        state = _database_snapshot(database)
    finally:
        temp.cleanup()

    assert calls["preflights"] == ["ML_CHAMPION", "ML_CHALLENGER"]
    assert calls["opens"] == [
        "pair-1-incumbent",
        "pair-1-challenger",
    ]
    assert state["cash"] == 2990.0
    assert len(state["positions"]) == 2
    assert len(state["previews"]) == 2

    assert receipt["paper_pair_entry_authorized"] is True
    assert receipt["paper_pair_entry_executed"] is True
    assert receipt["paper_pair_entry_completed"] is True
    assert receipt["atomic_pair_open_verified"] is True
    assert receipt["same_candidate_frame_verified"] is True
    assert receipt["same_decision_snapshot_verified"] is True
    assert receipt["equal_capital_verified"] is True
    assert receipt["chain_bound_verified"] is True
    assert receipt["no_lookahead_verified"] is True
    assert receipt["account_cash_quote_before"] == 5000.0
    assert receipt["account_cash_quote_after"] == 2990.0
    assert receipt["account_open_positions_before"] == 0
    assert receipt["account_open_positions_after"] == 2
    assert receipt["requires_post_pair_audit"] is True
    assert receipt["paper_evidence_collection_authorized"] is False
    assert receipt["paper_trading_authorized"] is False
    assert receipt["live_submit_authorized"] is False
    assert receipt["transaction_submission_performed"] is False
    assert receipt["new_live_capital_used"] is False
    assert receipt["phase8_promotion_authorized"] is False


def test_fresh_readiness_drift_fails_before_mutation(monkeypatch):
    temp, database, calls, run = _build(
        monkeypatch,
        fresh_readiness_mutator=lambda value: value.update(
            decision_observed_at="2026-09-30T00:41:00+00:00"
        ),
    )
    try:
        before = _database_snapshot(database)
        with pytest.raises(ValueError, match="differs from saved"):
            run()
        after = _database_snapshot(database)
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
        before = _database_snapshot(database)
        with pytest.raises(
            ValueError,
            match="challenger counterfactual preflight drifted",
        ):
            run()
        after = _database_snapshot(database)
    finally:
        temp.cleanup()

    assert calls["opens"] == []
    assert after == before


def test_second_open_failure_rolls_back_entire_pair(monkeypatch):
    temp, database, calls, run = _build(
        monkeypatch,
        fail_second_open=True,
    )
    try:
        before = _database_snapshot(database)
        with pytest.raises(
            ValueError,
            match="forced second pair open failure",
        ):
            run()
        after = _database_snapshot(database)
    finally:
        temp.cleanup()

    assert calls["opens"] == [
        "pair-1-incumbent",
        "pair-1-challenger",
    ]
    assert after == before


def test_resealed_receipt_cannot_authorize_ongoing_paper_trading(
    monkeypatch,
):
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
        MODULE.validate_phase8_paired_ml_paper_entry_execution_receipt(
            receipt
        )


def test_resealed_receipt_cannot_claim_live_submission(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["transaction_submission_performed"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="transaction_submission_performed=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_entry_execution_receipt(
            receipt
        )


def test_receipt_rejects_wrong_cash_delta(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["account_cash_quote_after"] = 3000.0
    _reseal(receipt)
    with pytest.raises(ValueError, match="cash delta mismatch"):
        MODULE.validate_phase8_paired_ml_paper_entry_execution_receipt(
            receipt
        )


def test_executor_uses_dedicated_one_shot_lock():
    assert MODULE.LOCK_PATH == Path(
        "/var/tmp/pio-phase8-paired-paper-entry-one-shot.lock"
    )


def test_executor_uses_bound_choices_not_model_rescoring():
    source = TOOL.read_text(encoding="utf-8")

    assert "score_ml_candidates(" not in source
    assert "load_registered_ml_v1(" not in source
    assert "build_current_ml_candidate_frame(" not in source
    assert "open_paired_ml_paper_entries(" not in source
    assert "PairedMLPaperChoice(" in source
    assert "_preflight_choice(" in source


def test_executor_has_no_scheduler_or_live_submit_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "paper_scheduler" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"transaction_submission_performed": False' in source
    assert '"new_live_capital_used": False' in source
