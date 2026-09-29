from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "apply_phase7_controlled_live_exit_final_state_reconciliation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "apply_phase7_controlled_live_exit_final_state_reconciliation",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


POSITION = "33333333333333333333333333333333"
POOL = "11111111111111111111111111111111"
EXIT_DECISION_ID = "01234567-89ab-4def-8123-456789abcdef"
SETTLEMENT_DECISION_ID = "PHASE7_EXIT_SETTLEMENT:" + "c" * 64


ACTIONS = [
    "INGEST_PRINCIPAL_EXIT_CHAIN",
    "INGEST_PRINCIPAL_EXIT_RECEIPT",
    "APPLY_PRINCIPAL_EXIT_EFFECT",
    "APPLY_PRINCIPAL_EXIT_POSITION",
    "INGEST_SETTLEMENT_CHAIN",
    "INGEST_SETTLEMENT_RECEIPT",
    "APPLY_SETTLEMENT_EFFECT",
    "FINALIZE_POSITION_CLOSURE",
    "BUILD_POSITION_OUTCOME",
]


def _saved_plan(database: Path) -> dict:
    return {
        "plan_sha256": "a" * 64,
        "reconciliation_plan_ready": True,
        "reconciliation_required": True,
        "requires_separate_reconciliation_apply": True,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_pio_database_modified": False,
        "planned_actions": list(ACTIONS),
        "principal_transaction_snapshot_sha256": "1" * 64,
        "settlement_transaction_snapshot_sha256": "2" * 64,
        "pio_database_path": str(database),
        "pio_database_sha256_before": "b" * 64,
        "pio_wal_sha256_before": None,
        "pio_shm_sha256_before": None,
        "observed_at": "2026-09-29T17:05:00+00:00",
        "opened_decision_id": "11111111-2222-4333-8444-555555555555",
        "principal_exit_decision_id": EXIT_DECISION_ID,
        "settlement_decision_id": SETTLEMENT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "principal_signature": "principal-sig",
        "settlement_signature": "settlement-sig",
        "private_target_state_sha256": "3" * 64,
    }


class _FakeModule:
    pass


class _FakeStorage:
    pass


class _Audit:
    def to_record(self):
        return {"clean": True}


def _runtime(observed: list[str]):
    def ingest_tx(storage, snapshot, observed_at=None):
        observed.append("chain:" + snapshot["signature"])
        return SimpleNamespace()

    def ingest_receipt(storage, payload, observed_at=None):
        observed.append("receipt:" + payload["decision_id"])
        return SimpleNamespace(
            chain_snapshot_reconciled=True,
            reused_existing=False,
        )

    def effect(storage, decision_id):
        observed.append("effect:" + decision_id)
        return SimpleNamespace(
            effect=SimpleNamespace(position_address=POSITION),
            reused_existing=False,
        )

    def position(storage, decision_id):
        observed.append("position:" + decision_id)
        return SimpleNamespace(
            position_address=POSITION,
            next_status="LIQUIDITY_REMOVED",
            reused_existing=False,
        )

    def closure(storage, *, decision_id, proof):
        observed.append("close:" + decision_id)
        return SimpleNamespace(
            position_address=POSITION,
            closed=True,
            reused_existing=False,
        )

    outcome_record = {
        "position_address": POSITION,
        "pool_address": POOL,
        "opened_decision_id": "open",
        "closed_decision_id": SETTLEMENT_DECISION_ID,
        "execution_count": 3,
        "token_x_wallet_delta_atomic": 10,
        "token_y_wallet_delta_atomic": 20,
        "composition_fee_x_atomic": 0,
        "composition_fee_y_atomic": 0,
        "earned_fee_x_atomic": 1,
        "earned_fee_y_atomic": 2,
        "reward_one_atomic": 3,
        "reward_two_atomic": 0,
        "network_fee_lamports": 31,
        "label_status": "ATOMIC_ONLY",
    }

    def outcome(storage, *, position_address):
        observed.append("outcome:" + position_address)
        return SimpleNamespace(
            outcome=SimpleNamespace(
                to_record=lambda: dict(outcome_record)
            ),
            reused_existing=False,
        )

    return {
        "Storage": _FakeStorage,
        "ingest_transaction_events": ingest_tx,
        "ingest_execution_receipt": ingest_receipt,
        "apply_live_execution_effect": effect,
        "apply_live_position_effect": position,
        "finalize_live_position_closure": closure,
        "build_live_position_outcome": outcome,
        "audit_execution_receipts": lambda storage: _Audit(),
        "audit_live_execution_ledger": lambda storage: _Audit(),
    }, outcome_record


class _FakePlanner:
    ACTION_INGEST_PRINCIPAL_CHAIN = ACTIONS[0]
    ACTION_INGEST_PRINCIPAL_RECEIPT = ACTIONS[1]
    ACTION_APPLY_PRINCIPAL_EFFECT = ACTIONS[2]
    ACTION_APPLY_PRINCIPAL_POSITION = ACTIONS[3]
    ACTION_INGEST_SETTLEMENT_CHAIN = ACTIONS[4]
    ACTION_INGEST_SETTLEMENT_RECEIPT = ACTIONS[5]
    ACTION_APPLY_SETTLEMENT_EFFECT = ACTIONS[6]
    ACTION_FINALIZE_CLOSURE = ACTIONS[7]
    ACTION_BUILD_OUTCOME = ACTIONS[8]

    saved = None
    runtime = None
    fresh_override = None
    db_state_calls = 0

    @staticmethod
    def _load_json(path, *, label):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @staticmethod
    def validate_exit_final_reconciliation_plan(value):
        assert isinstance(value, dict)

    @classmethod
    def build_exit_final_reconciliation_plan(cls, **kwargs):
        value = dict(cls.saved)
        if cls.fresh_override:
            value.update(cls.fresh_override)
        return value

    @classmethod
    def _load_reviewed(cls, source):
        return _FakeModule, _FakeModule, _FakeModule, cls.runtime

    @staticmethod
    def _inspect_transaction(*, executor_binary, signature, rpc_url):
        return {"signature": signature}

    @staticmethod
    def _validate_snapshot(snapshot, receipt):
        assert snapshot["signature"] == receipt["signature"]

    @staticmethod
    def _sha256_value(value):
        if value == {"signature": "principal-sig"}:
            return "1" * 64
        if value == {"signature": "settlement-sig"}:
            return "2" * 64
        return "3" * 64

    @classmethod
    def _database_state(cls, database):
        cls.db_state_calls += 1
        if cls.db_state_calls == 1:
            return {"database": "b" * 64, "wal": None, "shm": None}
        return {"database": "d" * 64, "wal": None, "shm": None}

    @staticmethod
    def _receipt_payload(receipt, *, decision_id):
        return {
            "decision_id": decision_id,
            "signature": receipt["signature"],
        }

    @staticmethod
    def _target_state(*args, **kwargs):
        return {"position": "CLOSED"}


def _artifact(signature: str) -> dict:
    return {
        "signature": signature,
        "closure_rpc_context_slot": 120,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    fresh_override: dict | None = None,
    db_before_override: str | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production")

        plan = _saved_plan(database)
        plan_path = _write(root / "plan.json", plan)
        principal_path = _write(
            root / "principal.json",
            _artifact("principal-sig"),
        )
        settlement_path = _write(
            root / "settlement.json",
            _artifact("settlement-sig"),
        )
        absence_path = _write(
            root / "absence.json",
            {
                "closure_rpc_context_slot": 120,
            },
        )

        observed = []
        runtime, outcome_record = _runtime(observed)
        _FakePlanner.saved = plan
        _FakePlanner.runtime = runtime
        _FakePlanner.fresh_override = fresh_override
        _FakePlanner.db_state_calls = 0

        if db_before_override is not None:
            original = _FakePlanner._database_state

            @classmethod
            def drift(cls, database):
                cls.db_state_calls += 1
                if cls.db_state_calls == 1:
                    return {
                        "database": db_before_override,
                        "wal": None,
                        "shm": None,
                    }
                return original(database)

            monkeypatch.setattr(
                _FakePlanner,
                "_database_state",
                drift,
            )

        monkeypatch.setattr(
            MODULE,
            "_load_planner",
            lambda source: _FakePlanner,
        )
        monkeypatch.setattr(
            MODULE,
            "_position_state",
            lambda database, **kwargs: (
                "CLOSED",
                True,
                True,
                dict(outcome_record),
            ),
        )

        report = MODULE.apply_exit_final_reconciliation(
            source_tree=ROOT,
            saved_plan_path=plan_path,
            expected_plan_sha256=plan["plan_sha256"],
            saved_principal_receipt_path=principal_path,
            expected_principal_receipt_sha256="4" * 64,
            saved_settlement_receipt_path=settlement_path,
            expected_settlement_receipt_sha256="5" * 64,
            saved_account_absence_proof_path=absence_path,
            expected_account_absence_proof_sha256="6" * 64,
            pio_database_path=database,
            executor_binary_path=database,
            expected_executor_binary_sha256="7" * 64,
            rpc_url="https://rpc.example.invalid",
            observed_at=plan["observed_at"],
        )
        return report, observed


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["apply_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_planner_dependency_is_exactly_pinned():
    path = ROOT / MODULE.PLANNER_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.PLANNER_TOOL
    ]


def test_apply_executes_exact_reviewed_action_order(monkeypatch):
    report, observed = _build(monkeypatch)

    assert report["planned_actions"] == ACTIONS
    assert report["applied_actions"] == ACTIONS
    assert report["target_state_matches_plan"] is True
    assert report["position_status_after_apply"] == "CLOSED"
    assert report["closure_proof_present_after_apply"] is True
    assert report["position_outcome_present_after_apply"] is True
    assert report["position_outcome"]["closed_decision_id"] == (
        SETTLEMENT_DECISION_ID
    )
    assert report["production_pio_database_modified"] is True
    assert report["transaction_submission_authorized"] is False
    assert report["phase7_promotion_authorized"] is False

    assert observed == [
        "chain:principal-sig",
        "receipt:" + EXIT_DECISION_ID,
        "effect:" + EXIT_DECISION_ID,
        "position:" + EXIT_DECISION_ID,
        "chain:settlement-sig",
        "receipt:" + SETTLEMENT_DECISION_ID,
        "effect:" + SETTLEMENT_DECISION_ID,
        "close:" + SETTLEMENT_DECISION_ID,
        "outcome:" + POSITION,
    ]


def test_fresh_plan_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="fresh .* plan differs"):
        _build(
            monkeypatch,
            fresh_override={"plan_sha256": "f" * 64},
        )


def test_database_drift_after_planning_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="Pio database changed"):
        _build(
            monkeypatch,
            db_before_override="f" * 64,
        )


def test_saved_plan_with_no_apply_is_rejected(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        database.write_bytes(b"production")
        plan = _saved_plan(database)
        plan["reconciliation_required"] = False
        plan["requires_separate_reconciliation_apply"] = False
        plan["planned_actions"] = []
        plan_path = _write(Path(tmp) / "plan.json", plan)

        monkeypatch.setattr(
            MODULE,
            "_load_planner",
            lambda source: _FakePlanner,
        )
        with pytest.raises(ValueError, match="requires no production apply"):
            MODULE.apply_exit_final_reconciliation(
                source_tree=ROOT,
                saved_plan_path=plan_path,
                expected_plan_sha256=plan["plan_sha256"],
                saved_principal_receipt_path=plan_path,
                expected_principal_receipt_sha256="4" * 64,
                saved_settlement_receipt_path=plan_path,
                expected_settlement_receipt_sha256="5" * 64,
                saved_account_absence_proof_path=plan_path,
                expected_account_absence_proof_sha256="6" * 64,
                pio_database_path=database,
                executor_binary_path=database,
                expected_executor_binary_sha256="7" * 64,
                rpc_url="https://rpc.example.invalid",
                observed_at=plan["observed_at"],
            )


def test_resealed_apply_cannot_authorize_submission(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_final_reconciliation_apply(report)


def test_resealed_apply_cannot_authorize_promotion(monkeypatch):
    report, _ = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_final_reconciliation_apply(report)


def test_apply_tool_has_no_transaction_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"automatic_resubmission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
