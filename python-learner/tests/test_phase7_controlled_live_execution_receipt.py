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
    / "build_phase7_controlled_live_execution_receipt.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_execution_receipt",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


RPC_URL = "https://rpc.example.invalid"
DECISION_ID = "11111111-2222-4333-8444-555555555555"
SIGNATURE = "sig-1"
POOL = "11111111111111111111111111111111"


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _confirmation(status: str) -> dict:
    post = {
        "PENDING": "SENT",
        "CONFIRMED": "CONFIRMED",
        "FAILED": "FAILED",
    }[status]
    return {
        "confirmation_sha256": "a" * 64,
        "observation_status": status,
        "execution_receipt_required": status in {"CONFIRMED", "FAILED"},
        "automatic_resubmission_performed": False,
        "saved_execution_once_sha256": "b" * 64,
        "decision_id": DECISION_ID,
        "signature": SIGNATURE,
        "post_intent_status": post,
    }


def _execution(binary: Path, binary_sha: str, execution_db: Path) -> dict:
    return {
        "execution_once_sha256": "b" * 64,
        "decision_id": DECISION_ID,
        "signature": SIGNATURE,
        "pool_address": POOL,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": binary_sha,
        "execution_database_path": str(execution_db),
    }


def _receipt(status: str) -> dict:
    confirmed = status == "CONFIRMED"
    return {
        "decision_id": DECISION_ID,
        "signature": SIGNATURE,
        "mode": "LIVE",
        "action": "ENTER",
        "pool_address": POOL,
        "intent_status": status,
        "slot": 123,
        "block_time": 456,
        "network_fee_lamports": 5000,
        "compute_units_consumed": 100000,
        "succeeded": confirmed,
        "event_count": 1,
        "add_request_count": 1,
        "rebalance_request_count": 0,
    }


def _build(monkeypatch, status: str) -> tuple[dict, list[list[str]]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        execution_db = root / "execution.db"
        execution_db.write_bytes(b"execution")

        confirmation = _confirmation(status)
        execution = _execution(binary, binary_sha, execution_db)
        confirmation_path = _write(root / "confirmation.json", confirmation)
        execution_path = _write(root / "execute-once.json", execution)

        class FakeConfirmation:
            @staticmethod
            def validate_confirmation_report(value):
                assert isinstance(value, dict)

        class FakeExecution:
            @staticmethod
            def validate_execution_once_report(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakeConfirmation, FakeExecution),
        )
        monkeypatch.setattr(
            MODULE,
            "_receipt_env",
            lambda *, rpc_url: {"SOLANA_RPC_URL": rpc_url},
        )
        monkeypatch.setattr(
            MODULE,
            "_intent_status",
            lambda **kwargs: {
                "status": status,
                "signature": SIGNATURE,
                "error": None if status == "CONFIRMED" else "chain rejected",
            },
        )
        monkeypatch.setattr(
            MODULE,
            "_database_state",
            lambda path: {
                "database": "1" * 64,
                "wal": None,
                "shm": None,
            },
        )

        commands: list[list[str]] = []

        def fake_run_json(command, **kwargs):
            commands.append(list(command))
            assert command[1] == MODULE.RECEIPT_COMMAND
            return copy.deepcopy(_receipt(status))

        monkeypatch.setattr(MODULE, "_run_json", fake_run_json)

        report = MODULE.build_receipt_artifact(
            source_tree=ROOT,
            saved_confirmation_path=confirmation_path,
            expected_confirmation_sha256=confirmation["confirmation_sha256"],
            saved_execution_once_path=execution_path,
            expected_execution_once_sha256=execution["execution_once_sha256"],
            rpc_url=RPC_URL,
        )
        return report, commands


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["receipt_artifact_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


@pytest.mark.parametrize("status", ["CONFIRMED", "FAILED"])
def test_terminal_confirmation_builds_one_read_only_receipt(
    monkeypatch,
    status,
):
    report, commands = _build(monkeypatch, status)

    assert len(commands) == 1
    assert commands[0][1] == MODULE.RECEIPT_COMMAND
    assert report["receipt_ready"] is True
    assert report["receipt"]["intent_status"] == status
    assert report["receipt"]["succeeded"] is (status == "CONFIRMED")
    assert report["execution_database_unchanged"] is True
    assert report["automatic_resubmission_performed"] is False
    assert report["live_capital_effect_reconciled"] is False
    assert report["requires_position_state_reconciliation"] is True
    assert report["requires_learning_label_reconciliation"] is True
    assert report["phase7_promotion_persisted"] is False


def test_pending_confirmation_cannot_build_receipt(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        binary = root / "executor"
        binary.write_bytes(b"x")
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        execution_db = root / "execution.db"
        execution_db.write_bytes(b"x")
        confirmation = _confirmation("PENDING")
        execution = _execution(binary, binary_sha, execution_db)
        cp = _write(root / "confirmation.json", confirmation)
        ep = _write(root / "execution.json", execution)

        class FakeConfirmation:
            @staticmethod
            def validate_confirmation_report(value):
                pass

        class FakeExecution:
            @staticmethod
            def validate_execution_once_report(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakeConfirmation, FakeExecution),
        )

        with pytest.raises(ValueError, match="terminal confirmation outcome"):
            MODULE.build_receipt_artifact(
                source_tree=ROOT,
                saved_confirmation_path=cp,
                expected_confirmation_sha256=confirmation["confirmation_sha256"],
                saved_execution_once_path=ep,
                expected_execution_once_sha256=execution["execution_once_sha256"],
                rpc_url=RPC_URL,
            )


def test_receipt_validation_rejects_success_mismatch():
    receipt = _receipt("CONFIRMED")
    receipt["succeeded"] = False

    with pytest.raises(ValueError, match="success outcome mismatch"):
        MODULE._validate_receipt(
            receipt,
            decision_id=DECISION_ID,
            signature=SIGNATURE,
            pool_address=POOL,
            expected_status="CONFIRMED",
        )


def test_receipt_validation_rejects_non_enter_action():
    receipt = _receipt("CONFIRMED")
    receipt["action"] = "REBALANCE"

    with pytest.raises(ValueError, match="must be ENTER"):
        MODULE._validate_receipt(
            receipt,
            decision_id=DECISION_ID,
            signature=SIGNATURE,
            pool_address=POOL,
            expected_status="CONFIRMED",
        )


def test_resealed_receipt_cannot_claim_capital_reconciled(monkeypatch):
    report, _ = _build(monkeypatch, "CONFIRMED")
    report["live_capital_effect_reconciled"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="live_capital_effect_reconciled=false"):
        MODULE.validate_receipt_artifact(report)


def test_resealed_receipt_cannot_claim_phase7_promotion(monkeypatch):
    report, _ = _build(monkeypatch, "CONFIRMED")
    report["phase7_promotion_persisted"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase7_promotion_persisted=false"):
        MODULE.validate_receipt_artifact(report)


def test_receipt_environment_strips_signing_and_submit_opt_in(monkeypatch):
    monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")
    monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
    monkeypatch.setenv("RPC_URL", "https://fallback.invalid")

    env = MODULE._receipt_env(rpc_url=RPC_URL)

    assert MODULE.KEYPAIR_ENV not in env
    assert MODULE.LIVE_SUBMIT_ENV not in env
    assert "RPC_URL" not in env
    assert env["SOLANA_RPC_URL"] == RPC_URL


def test_receipt_tool_has_no_submission_confirmation_or_recovery_mutation():
    source = TOOL.read_text(encoding="utf-8")

    assert 'RECEIPT_COMMAND = "execution-receipt"' in source
    assert '"controlled-live-submit-once"' not in source
    assert '"controlled-live-submit"' not in source
    assert '"execution-confirmation"' not in source
    assert '"execution-recovery"' not in source
    assert '"automatic_resubmission_performed": False' in source
    assert '"live_capital_effect_reconciled": False' in source
    assert '"phase7_promotion_persisted": False' in source
