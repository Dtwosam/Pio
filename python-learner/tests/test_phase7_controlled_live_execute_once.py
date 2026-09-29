from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "execute_phase7_controlled_live_transaction_once.py"
)

SPEC = importlib.util.spec_from_file_location(
    "execute_phase7_controlled_live_transaction_once",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


RPC_URL = "https://rpc.example.invalid"
DECISION_ID = "11111111-2222-4333-8444-555555555555"
POOL = "11111111111111111111111111111111"
WALLET = "22222222222222222222222222222222"


def _admission(
    *,
    binary: Path,
    binary_sha: str,
    height: int,
    admission_sha: str,
) -> dict:
    return {
        "admission_sha256": admission_sha,
        "execution_admission_ready": True,
        "requires_separate_single_shot_submitter": True,
        "fresh_readiness_sha256": ("9" if height == 100 else "8") * 64,
        "fresh_current_block_height": height,
        "fresh_block_height_remaining": 120 - height,
        "decision_id": DECISION_ID,
        "pool_address": POOL,
        "executor_wallet_pubkey": WALLET,
        "prepared_transaction_sha256": "1" * 64,
        "final_simulation_sha256": "2" * 64,
        "execution_intent_snapshot_sha256": "3" * 64,
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "blockhash_not_expired": True,
        "human_transaction_execution_authorization_verified": True,
        "transaction_authorization_not_expired": True,
        "executor_keypair_identity_verified": True,
    }


def _write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    rpc_accepted: bool = True,
) -> tuple[dict, list[list[str]]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        pio_db = production / "data" / "pio.db"
        pio_db.write_bytes(b"pio")
        execution_db = root / "execution.db"
        execution_db.write_bytes(b"execution-before")
        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor-binary")
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        controlled = {"enabled": True, "allowed_pool_addresses": [POOL]}
        guard = {"require_unsigned": True, "required_fee_payer": WALLET}
        controlled_path = _write_json(root / "controlled.json", controlled)
        guard_path = _write_json(root / "guard.json", guard)

        saved = _admission(
            binary=binary,
            binary_sha=binary_sha,
            height=100,
            admission_sha="a" * 64,
        )
        fresh = _admission(
            binary=binary,
            binary_sha=binary_sha,
            height=101,
            admission_sha="b" * 64,
        )
        saved_path = _write_json(root / "admission.json", saved)

        presubmit = {
            "presubmit_evidence_ready": True,
            "decision_id": DECISION_ID,
            "transaction_guard_config_sha256": MODULE._sha256_value(guard),
            "controlled_live_config_sha256": MODULE._sha256_value(controlled),
            "pio_database_path": str(pio_db),
            "execution_database_path": str(execution_db),
        }
        presubmit_path = _write_json(root / "presubmit.json", presubmit)

        class FakeAdmission:
            @staticmethod
            def validate_execution_admission(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_execution_admission(**kwargs):
                return copy.deepcopy(fresh)

        class FakePresubmit:
            @staticmethod
            def validate_phase7_presubmit_evidence_gate(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakeAdmission, FakePresubmit),
        )
        monkeypatch.setattr(
            MODULE,
            "_executor_env",
            lambda *, rpc_url: {"SOLANA_RPC_URL": rpc_url},
        )

        intent_calls = {"count": 0}

        def fake_intent_status(**kwargs):
            intent_calls["count"] += 1
            if intent_calls["count"] == 1:
                return {
                    "status": "SIMULATION_PASSED",
                    "signature": None,
                    "error": None,
                }
            return {
                "status": "SENT",
                "signature": "sig-1",
                "error": None,
            }

        monkeypatch.setattr(MODULE, "_intent_status", fake_intent_status)

        db_calls = {"execution": 0}

        def fake_database_state(path):
            if Path(path) == pio_db:
                return {
                    "database": "4" * 64,
                    "wal": None,
                    "shm": None,
                }
            assert Path(path) == execution_db
            db_calls["execution"] += 1
            digest = "5" * 64 if db_calls["execution"] == 1 else "6" * 64
            return {
                "database": digest,
                "wal": None,
                "shm": None,
            }

        monkeypatch.setattr(MODULE, "_database_state", fake_database_state)

        commands: list[list[str]] = []

        def fake_run_json(command, **kwargs):
            commands.append(list(command))
            assert MODULE.SUBMIT_COMMAND in command
            report = {
                "decision_id": DECISION_ID,
                "signature": "sig-1",
                "reused_persisted_signature": False,
                "rpc_accepted": rpc_accepted,
                "rpc_error": None if rpc_accepted else "timeout after submit",
                "intent_status": "SENT",
            }
            return (0 if rpc_accepted else 2), report

        monkeypatch.setattr(MODULE, "_run_json", fake_run_json)

        dummy = root / "dummy.json"
        dummy.write_text("{}", encoding="utf-8")
        args = {
            "repository": production,
            "source_tree": ROOT,
            "saved_execution_admission_path": saved_path,
            "expected_saved_admission_sha256": saved["admission_sha256"],
            "saved_transaction_execution_readiness_path": dummy,
            "expected_saved_readiness_sha256": "7" * 64,
            "phase6_post_promotion_audit_path": dummy,
            "saved_phase7_evidence_status_path": dummy,
            "saved_phase7_evidence_plan_path": dummy,
            "saved_input_preflight_path": dummy,
            "controlled_live_config_path": controlled_path,
            "proposal_path": dummy,
            "executor_wallet_pubkey": WALLET,
            "saved_controlled_live_authorization_verification_path": dummy,
            "controlled_live_signed_payload_path": dummy,
            "controlled_live_signature_path": dummy,
            "controlled_live_allowed_signers_path": dummy,
            "expected_controlled_live_allowed_signers_sha256": "d" * 64,
            "saved_authorization_readiness_path": dummy,
            "expected_authorization_readiness_sha256": "e" * 64,
            "execution_database_path": execution_db,
            "saved_presubmit_gate_path": presubmit_path,
            "transaction_request_path": dummy,
            "saved_transaction_authorization_verification_path": dummy,
            "transaction_signed_payload_path": dummy,
            "transaction_signature_path": dummy,
            "transaction_allowed_signers_path": dummy,
            "expected_transaction_allowed_signers_sha256": "f" * 64,
            "executor_binary_path": binary,
            "expected_executor_binary_sha256": binary_sha,
            "transaction_guard_config_path": guard_path,
            "rpc_url": RPC_URL,
            "now": "2026-09-29T08:30:00Z",
        }
        report = MODULE.execute_once(**args)
        return report, commands


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["execution_once_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_successful_execute_once_uses_exactly_one_first_submission(monkeypatch):
    report, commands = _build(monkeypatch, rpc_accepted=True)

    assert len(commands) == 1
    assert commands[0][1] == MODULE.SUBMIT_COMMAND
    assert report["submission_attempted_once"] is True
    assert report["automatic_retry_performed"] is False
    assert report["reused_persisted_signature"] is False
    assert report["transaction_signing_performed"] is True
    assert report["transaction_submission_attempted"] is True
    assert report["rpc_accepted"] is True
    assert report["post_intent_status"] == "SENT"
    assert report["live_capital_movement_confirmed"] is False
    assert report["confirmation_receipt_reconciliation_required"] is True
    assert report["confirmation_recovery_required_if_rpc_unaccepted"] is False


def test_ambiguous_rpc_outcome_stays_sent_without_retry(monkeypatch):
    report, commands = _build(monkeypatch, rpc_accepted=False)

    assert len(commands) == 1
    assert commands[0][1] == MODULE.SUBMIT_COMMAND
    assert report["submission_process_returncode"] == 2
    assert report["rpc_accepted"] is False
    assert report["rpc_error"] == "timeout after submit"
    assert report["post_intent_status"] == "SENT"
    assert report["automatic_retry_performed"] is False
    assert report["confirmation_recovery_required_if_rpc_unaccepted"] is True
    assert report["live_capital_movement_confirmed"] is False


def test_submission_report_rejects_reused_signature():
    report = {
        "decision_id": DECISION_ID,
        "signature": "sig-1",
        "reused_persisted_signature": True,
        "rpc_accepted": True,
        "rpc_error": None,
        "intent_status": "SENT",
    }

    with pytest.raises(ValueError, match="reused a signature"):
        MODULE._validate_submission_report(
            report,
            decision_id=DECISION_ID,
            returncode=0,
        )


def test_resealed_report_cannot_claim_automatic_retry(monkeypatch):
    report, _ = _build(monkeypatch)
    report["automatic_retry_performed"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="automatic_retry_performed=false"):
        MODULE.validate_execution_once_report(report)


def test_resealed_report_cannot_claim_confirmed_capital_movement(monkeypatch):
    report, _ = _build(monkeypatch)
    report["live_capital_movement_confirmed"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="live_capital_movement_confirmed=false"):
        MODULE.validate_execution_once_report(report)


def test_resealed_report_cannot_claim_phase7_promotion(monkeypatch):
    report, _ = _build(monkeypatch)
    report["phase7_promotion_persisted"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase7_promotion_persisted=false"):
        MODULE.validate_execution_once_report(report)


def test_wrapper_exposes_only_first_submission_command():
    source = TOOL.read_text(encoding="utf-8")

    assert 'SUBMIT_COMMAND = "controlled-live-submit-once"' in source
    assert 'SUBMIT_COMMAND = "controlled-live-submit"' not in source
    assert '"execution-confirmation"' not in source
    assert '"execution-recovery"' not in source
    assert '"automatic_retry_performed": False' in source
    assert '"live_capital_movement_confirmed": False' in source
    assert '"phase7_promotion_persisted": False' in source
