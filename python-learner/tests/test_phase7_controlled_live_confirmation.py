from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "reconcile_phase7_controlled_live_transaction_confirmation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "reconcile_phase7_controlled_live_transaction_confirmation",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


RPC_URL = "https://rpc.example.invalid"
DECISION_ID = "11111111-2222-4333-8444-555555555555"
SIGNATURE = "sig-1"


def _execution(binary: Path, binary_sha: str) -> dict:
    return {
        "execution_once_sha256": "a" * 64,
        "submission_attempted_once": True,
        "post_intent_status": "SENT",
        "automatic_retry_performed": False,
        "live_capital_movement_confirmed": False,
        "decision_id": DECISION_ID,
        "signature": SIGNATURE,
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": binary_sha,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "pio_database_path": "",
        "execution_database_path": "",
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, status: str) -> tuple[dict, list[list[str]]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        pio = root / "pio.db"
        pio.write_bytes(b"pio")
        execution_db = root / "execution.db"
        execution_db.write_bytes(b"execution")

        execution = _execution(binary, binary_sha)
        execution["pio_database_path"] = str(pio)
        execution["execution_database_path"] = str(execution_db)
        execution_path = _write(root / "execution-once.json", execution)

        class FakeExecute:
            @staticmethod
            def validate_execution_once_report(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_execute_once_module",
            lambda source: FakeExecute,
        )
        monkeypatch.setattr(
            MODULE,
            "_confirmation_env",
            lambda *, rpc_url: {"SOLANA_RPC_URL": rpc_url},
        )

        intent_calls = {"count": 0}

        def fake_status(**kwargs):
            intent_calls["count"] += 1
            if intent_calls["count"] == 1:
                return {
                    "status": "SENT",
                    "signature": SIGNATURE,
                    "error": None,
                }
            return {
                "status": {
                    "PENDING": "SENT",
                    "CONFIRMED": "CONFIRMED",
                    "FAILED": "FAILED",
                }[status],
                "signature": SIGNATURE,
                "error": "chain rejected" if status == "FAILED" else None,
            }

        monkeypatch.setattr(MODULE, "_intent_status", fake_status)

        db_calls = {"execution": 0}

        def fake_db_state(path):
            if Path(path) == pio:
                return {"database": "1" * 64, "wal": None, "shm": None}
            db_calls["execution"] += 1
            if status == "PENDING":
                digest = "2" * 64
            else:
                digest = "2" * 64 if db_calls["execution"] == 1 else "3" * 64
            return {"database": digest, "wal": None, "shm": None}

        monkeypatch.setattr(MODULE, "_database_state", fake_db_state)

        commands: list[list[str]] = []

        def fake_run_json(command, **kwargs):
            commands.append(list(command))
            assert command[1] == MODULE.CONFIRM_COMMAND
            if status == "PENDING":
                return 0, {
                    "decision_id": DECISION_ID,
                    "signature": SIGNATURE,
                    "observation": {"status": "PENDING"},
                    "intent_status": "SENT",
                    "changed": False,
                }
            if status == "CONFIRMED":
                return 0, {
                    "decision_id": DECISION_ID,
                    "signature": SIGNATURE,
                    "observation": {"status": "CONFIRMED"},
                    "intent_status": "CONFIRMED",
                    "changed": True,
                }
            return 2, {
                "decision_id": DECISION_ID,
                "signature": SIGNATURE,
                "observation": {
                    "status": "FAILED",
                    "error": "chain rejected",
                },
                "intent_status": "FAILED",
                "changed": True,
            }

        monkeypatch.setattr(MODULE, "_run_json", fake_run_json)

        report = MODULE.reconcile_confirmation(
            source_tree=ROOT,
            saved_execution_once_path=execution_path,
            expected_execution_once_sha256=execution["execution_once_sha256"],
            rpc_url=RPC_URL,
        )
        return report, commands


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["confirmation_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


@pytest.mark.parametrize(
    ("status", "post_status", "mutated", "receipt_required"),
    [
        ("PENDING", "SENT", False, False),
        ("CONFIRMED", "CONFIRMED", True, True),
        ("FAILED", "FAILED", True, True),
    ],
)
def test_confirmation_outcomes_never_resubmit(
    monkeypatch,
    status,
    post_status,
    mutated,
    receipt_required,
):
    report, commands = _build(monkeypatch, status)

    assert len(commands) == 1
    assert commands[0][1] == MODULE.CONFIRM_COMMAND
    assert report["observation_status"] == status
    assert report["post_intent_status"] == post_status
    assert report["execution_database_mutated"] is mutated
    assert report["execution_receipt_required"] is receipt_required
    assert report["automatic_resubmission_performed"] is False
    assert report["live_capital_effect_reconciled"] is False


def test_pending_requires_recheck(monkeypatch):
    report, _ = _build(monkeypatch, "PENDING")

    assert report["confirmation_pending"] is True
    assert report["confirmation_recheck_required"] is True
    assert report["transaction_chain_confirmation_observed"] is False
    assert report["transaction_failure_observed"] is False


def test_confirmed_requires_receipt_not_promotion(monkeypatch):
    report, _ = _build(monkeypatch, "CONFIRMED")

    assert report["transaction_chain_confirmation_observed"] is True
    assert report["execution_receipt_required"] is True
    assert report["phase7_promotion_separate"] is True
    assert report["phase7_promotion_persisted"] is False
    assert report["live_capital_effect_reconciled"] is False


def test_failed_requires_receipt_and_no_retry(monkeypatch):
    report, _ = _build(monkeypatch, "FAILED")

    assert report["transaction_failure_observed"] is True
    assert report["execution_receipt_required"] is True
    assert report["automatic_resubmission_performed"] is False


def test_resealed_confirmation_cannot_claim_resubmission(monkeypatch):
    report, _ = _build(monkeypatch, "CONFIRMED")
    report["automatic_resubmission_performed"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="automatic_resubmission_performed=false"):
        MODULE.validate_confirmation_report(report)


def test_resealed_confirmation_cannot_claim_reconciled_capital(monkeypatch):
    report, _ = _build(monkeypatch, "CONFIRMED")
    report["live_capital_effect_reconciled"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="live_capital_effect_reconciled=false"):
        MODULE.validate_confirmation_report(report)


def test_confirmation_environment_strips_signing_and_submit_opt_in(monkeypatch):
    monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")
    monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
    monkeypatch.setenv("RPC_URL", "https://fallback.invalid")

    env = MODULE._confirmation_env(rpc_url=RPC_URL)

    assert MODULE.KEYPAIR_ENV not in env
    assert MODULE.LIVE_SUBMIT_ENV not in env
    assert "RPC_URL" not in env
    assert env["SOLANA_RPC_URL"] == RPC_URL


def test_confirmation_tool_has_no_submission_or_recovery_command():
    source = TOOL.read_text(encoding="utf-8")

    assert 'CONFIRM_COMMAND = "execution-confirmation"' in source
    assert '"controlled-live-submit-once"' not in source
    assert '"controlled-live-submit"' not in source
    assert '"execution-recovery"' not in source
    assert '"automatic_resubmission_performed": False' in source
    assert '"live_capital_effect_reconciled": False' in source
