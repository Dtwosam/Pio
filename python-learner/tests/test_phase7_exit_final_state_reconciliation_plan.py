from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase7_controlled_live_exit_final_state_reconciliation_plan.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_final_state_reconciliation_plan",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


OPENED_DECISION_ID = "11111111-2222-4333-8444-555555555555"
EXIT_DECISION_ID = "01234567-89ab-4def-8123-456789abcdef"
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"
WALLET = "44444444444444444444444444444444"
RPC_URL = "https://rpc.example.invalid"


def _snapshot(signature: str, slot: int, block_time: int, fee: int, compute: int, events: int):
    return {
        "signature": signature,
        "slot": slot,
        "block_time": block_time,
        "network_fee_lamports": fee,
        "compute_units_consumed": compute,
        "succeeded": True,
        "events": [{"event": index} for index in range(events)],
        "add_requests": [],
        "rebalance_requests": [],
    }


PRINCIPAL_SNAPSHOT = _snapshot("principal-sig", 100, 1000, 10, 20, 1)
SETTLEMENT_SNAPSHOT = _snapshot("settlement-sig", 110, 1100, 11, 22, 3)


def _principal() -> dict:
    return {
        "receipt_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "signature": "principal-sig",
        "terminal_receipt_status": "CONFIRMED",
        "transaction_succeeded": True,
        "confirmation_matches_transaction_snapshot": True,
        "automatic_resubmission_performed": False,
        "production_pio_database_modified": False,
        "remove_liquidity_event_matches_execution": True,
        "transaction_slot": 100,
        "block_time": 1000,
        "network_fee_lamports": 10,
        "compute_units_consumed": 20,
        "event_count": 1,
        "add_request_count": 0,
        "rebalance_request_count": 0,
        "transaction_snapshot_sha256": MODULE._sha256_value(PRINCIPAL_SNAPSHOT),
    }


def _settlement() -> dict:
    return {
        "receipt_sha256": "b" * 64,
        "saved_request_sha256": "c" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "destination_config_sha256": "d" * 64,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "signature": "settlement-sig",
        "terminal_receipt_status": "CONFIRMED",
        "transaction_succeeded": True,
        "confirmation_matches_transaction_snapshot": True,
        "automatic_resubmission_performed": False,
        "production_pio_database_modified": False,
        "settlement_events_match_request": True,
        "transaction_slot": 110,
        "block_time": 1100,
        "network_fee_lamports": 11,
        "compute_units_consumed": 22,
        "event_count": 3,
        "add_request_count": 0,
        "rebalance_request_count": 0,
        "transaction_snapshot_sha256": MODULE._sha256_value(SETTLEMENT_SNAPSHOT),
    }


def _absence() -> dict:
    return {
        "proof_sha256": "e" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "destination_config_sha256": "d" * 64,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "settlement_signature": "settlement-sig",
        "settlement_transaction_slot": 110,
        "closure_rpc_context_slot": 111,
        "position_account_absence_proven": True,
        "post_exit_state_reconciliation_ready": True,
        "production_pio_database_modified": False,
    }


class _FakePrincipalModule:
    @staticmethod
    def validate_exit_terminal_receipt(value):
        assert isinstance(value, dict)


class _FakeSettlementModule:
    @staticmethod
    def validate_exit_settlement_terminal_receipt(value):
        assert isinstance(value, dict)


class _FakeAbsenceModule:
    @staticmethod
    def validate_exit_settlement_account_absence_proof(value):
        assert isinstance(value, dict)


class _FakeStorage:
    def __init__(self, path):
        self.path = Path(path)


@dataclass
class _Outcome:
    position_address: str
    closed_decision_id: str
    label_status: str = "ATOMIC_ONLY"

    def to_record(self):
        return {
            "position_address": self.position_address,
            "pool_address": POOL,
            "opened_decision_id": OPENED_DECISION_ID,
            "closed_decision_id": self.closed_decision_id,
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
            "label_status": self.label_status,
        }


class _Audit:
    def to_record(self):
        return {"clean": True}


def _runtime(observed: list[str]) -> dict:
    def ingest_tx(storage, snapshot, observed_at=None):
        observed.append("chain:" + snapshot["signature"])
        return SimpleNamespace(signature=snapshot["signature"], events=len(snapshot["events"]))

    def ingest_receipt(storage, receipt, observed_at=None):
        observed.append("receipt:" + receipt["decision_id"])
        return SimpleNamespace(
            decision_id=receipt["decision_id"],
            signature=receipt["signature"],
            reused_existing=False,
            chain_snapshot_reconciled=True,
        )

    def apply_effect(storage, decision_id):
        observed.append("effect:" + decision_id)
        return SimpleNamespace(
            effect=SimpleNamespace(position_address=POSITION),
            reused_existing=False,
        )

    def apply_position(storage, decision_id):
        observed.append("position:" + decision_id)
        return SimpleNamespace(
            position_address=POSITION,
            next_status="LIQUIDITY_REMOVED",
            reused_existing=False,
        )

    def close(storage, *, decision_id, proof):
        observed.append("close:" + decision_id)
        assert proof == {
            "position": POSITION,
            "closed": True,
            "rpc_context_slot": 111,
        }
        return SimpleNamespace(
            position_address=POSITION,
            closed=True,
            reused_existing=False,
        )

    def outcome(storage, *, position_address):
        observed.append("outcome:" + position_address)
        settlement_decision = "PHASE7_EXIT_SETTLEMENT:" + "c" * 64
        return SimpleNamespace(
            outcome=_Outcome(position_address, settlement_decision),
            reused_existing=False,
        )

    return {
        "Storage": _FakeStorage,
        "ingest_transaction_events": ingest_tx,
        "ingest_execution_receipt": ingest_receipt,
        "apply_live_execution_effect": apply_effect,
        "apply_live_position_effect": apply_position,
        "finalize_live_position_closure": close,
        "build_live_position_outcome": outcome,
        "audit_execution_receipts": lambda storage: _Audit(),
        "audit_live_execution_ledger": lambda storage: _Audit(),
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _all_false_pre() -> dict[str, bool]:
    return {
        "principal_chain": False,
        "principal_receipt": False,
        "principal_effect": False,
        "principal_position_event": False,
        "settlement_chain": False,
        "settlement_receipt": False,
        "settlement_effect": False,
        "closure_proof": False,
        "close_event": False,
        "position_closed": False,
        "position_outcome": False,
    }


def _build(
    monkeypatch,
    *,
    principal_override: dict | None = None,
    settlement_override: dict | None = None,
    absence_override: dict | None = None,
    pre: dict[str, bool] | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        principal = _principal()
        settlement = _settlement()
        absence = _absence()
        if principal_override:
            principal.update(principal_override)
        if settlement_override:
            settlement.update(settlement_override)
        if absence_override:
            absence.update(absence_override)

        principal_path = _write(root / "principal.json", principal)
        settlement_path = _write(root / "settlement.json", settlement)
        absence_path = _write(root / "absence.json", absence)

        database = root / "pio.db"
        database.write_bytes(b"production-db")
        database_sha = hashlib.sha256(database.read_bytes()).hexdigest()

        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        observed: list[str] = []
        runtime = _runtime(observed)
        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (
                _FakePrincipalModule,
                _FakeSettlementModule,
                _FakeAbsenceModule,
                runtime,
            ),
        )
        monkeypatch.setattr(
            MODULE,
            "_inspect_transaction",
            lambda **kwargs: (
                dict(PRINCIPAL_SNAPSHOT)
                if kwargs["signature"] == "principal-sig"
                else dict(SETTLEMENT_SNAPSHOT)
            ),
        )
        monkeypatch.setattr(
            MODULE,
            "_database_state",
            lambda path: {
                "database": database_sha,
                "wal": None,
                "shm": None,
            },
        )
        monkeypatch.setattr(
            MODULE,
            "_backup_database",
            lambda source, destination: shutil.copyfile(source, destination),
        )
        monkeypatch.setattr(
            MODULE,
            "_pre_state",
            lambda *args, **kwargs: dict(pre or _all_false_pre()),
        )
        monkeypatch.setattr(
            MODULE,
            "_target_state",
            lambda *args, **kwargs: {
                "position": "CLOSED",
                "outcome": "ATOMIC_ONLY",
            },
        )

        report = MODULE.build_exit_final_reconciliation_plan(
            source_tree=ROOT,
            saved_principal_receipt_path=principal_path,
            expected_principal_receipt_sha256=principal["receipt_sha256"],
            saved_settlement_receipt_path=settlement_path,
            expected_settlement_receipt_sha256=settlement["receipt_sha256"],
            saved_account_absence_proof_path=absence_path,
            expected_account_absence_proof_sha256=absence["proof_sha256"],
            pio_database_path=database,
            executor_binary_path=binary,
            expected_executor_binary_sha256=binary_sha,
            rpc_url=RPC_URL,
            observed_at="2026-09-29T16:55:00+00:00",
        )
        return report, observed


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["plan_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_final_reconciliation_private_replay_closes_position(monkeypatch):
    report, observed = _build(monkeypatch)

    assert report["planned_actions"] == list(MODULE.ACTION_ORDER)
    assert report["reconciliation_required"] is True
    assert report["requires_separate_reconciliation_apply"] is True
    assert report["private_replay_succeeded"] is True
    assert report["principal_position_next_status"] == "LIQUIDITY_REMOVED"
    assert report["closure_status"] == "CLOSED"
    assert report["position_outcome"]["label_status"] == "ATOMIC_ONLY"
    assert report["production_pio_database_modified"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["phase7_promotion_authorized"] is False

    assert observed[0] == "chain:principal-sig"
    assert "position:" + EXIT_DECISION_ID in observed
    assert observed[-1] == "outcome:" + POSITION


def test_already_reconciled_state_requires_no_apply(monkeypatch):
    pre = {key: True for key in _all_false_pre()}
    report, _ = _build(monkeypatch, pre=pre)

    assert report["planned_actions"] == []
    assert report["reconciliation_required"] is False
    assert report["requires_separate_reconciliation_apply"] is False
    assert report["private_replay_succeeded"] is True


def test_partial_closure_state_fails_closed(monkeypatch):
    pre = _all_false_pre()
    pre["closure_proof"] = True

    with pytest.raises(ValueError, match="partial settlement closure state"):
        _build(monkeypatch, pre=pre)


def test_settlement_cannot_predate_principal_exit(monkeypatch):
    with pytest.raises(ValueError, match="predates principal EXIT"):
        _build(
            monkeypatch,
            settlement_override={"transaction_slot": 99},
            absence_override={"settlement_transaction_slot": 99},
        )


def test_account_absence_must_be_proven(monkeypatch):
    with pytest.raises(ValueError, match="absence has not been proven"):
        _build(
            monkeypatch,
            absence_override={"position_account_absence_proven": False},
        )


def test_destination_config_binding_mismatch_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="destination-config binding mismatch"):
        _build(
            monkeypatch,
            absence_override={"destination_config_sha256": "f" * 64},
        )


def test_rpc_endpoint_binding_must_match(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            principal_override={"rpc_endpoint_sha256": "f" * 64},
            settlement_override={"rpc_endpoint_sha256": "f" * 64},
            absence_override={"rpc_endpoint_sha256": "f" * 64},
        )


def test_snapshot_digest_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="snapshot digest differs"):
        _build(
            monkeypatch,
            principal_override={"transaction_snapshot_sha256": "f" * 64},
        )


def test_resealed_plan_cannot_authorize_submission(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_final_reconciliation_plan(report)


def test_resealed_plan_cannot_authorize_promotion(monkeypatch):
    report, _ = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_final_reconciliation_plan(report)


def test_planner_is_private_replay_only():
    source = TOOL.read_text(encoding="utf-8")

    assert 'mode=ro' in source
    assert "_backup_database(database, private_db)" in source
    assert "finalize_live_position_closure" in source
    assert "build_live_position_outcome" in source
    assert "load_executor_keypair" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
    assert '"production_pio_database_modified": False' in source
