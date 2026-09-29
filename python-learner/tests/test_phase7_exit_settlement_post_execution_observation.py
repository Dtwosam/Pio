from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "observe_phase7_controlled_live_verified_exit_settlement_post_execution.py"
)

SPEC = importlib.util.spec_from_file_location(
    "observe_phase7_controlled_live_verified_exit_settlement_post_execution",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


RPC_URL = "https://rpc.example.invalid"
OPENED_DECISION_ID = "11111111-2222-4333-8444-555555555555"
EXIT_DECISION_ID = "01234567-89ab-4def-8123-456789abcdef"
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"
WALLET = "44444444444444444444444444444444"
SIGNATURE = (
    "5NfR2yR4tXbFp7YqZ3wDqM7K4k8Qm9vY6YV7A7A7A7A7"
    "A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A"
)


class _FakeExecutionModule:
    @staticmethod
    def validate_verified_single_execution(value):
        assert isinstance(value, dict)


def _execution(
    journal_path: Path,
    *,
    accepted: bool = True,
) -> dict:
    status = "RPC_ACCEPTED" if accepted else "RPC_UNCERTAIN"
    return {
        "execution_sha256": "a" * 64,
        "saved_request_sha256": "b" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "destination_config_sha256": "e" * 64,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "final_settlement_transaction_sha256": "c" * 64,
        "signed_transaction_sha256": MODULE._sha256_text(
            "signed-transaction"
        ),
        "signature": SIGNATURE,
        "submission_journal_path_sha256": MODULE._sha256_text(
            str(journal_path)
        ),
        "journal_status": status,
        "journal_claim_persisted_before_rpc_send": True,
        "automatic_retry_performed": False,
        "exact_signed_transaction_verified": True,
        "transaction_signing_performed": False,
        "submission_attempted_once": True,
        "rpc_accepted": accepted,
        "rpc_error": None if accepted else "network outcome unknown",
        "uncertain_rpc_requires_recovery": True,
        "signed_verification_valid": True,
        "signed_verification_matches_request": True,
        "dedicated_submission_journal_used": True,
        "journal_mutated": True,
        "production_pio_database_unchanged": True,
        "requires_post_settlement_confirmation": True,
        "requires_post_close_account_absence_proof": True,
        "requires_post_exit_state_reconciliation": True,
        "live_capital_movement_confirmed": False,
        "phase7_promotion_persisted": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }


def _write_journal(
    path: Path,
    execution: dict,
    *,
    signature: str = SIGNATURE,
    status: str | None = None,
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE phase7_exit_settlement_submission_journal (
                request_sha256 TEXT PRIMARY KEY,
                final_settlement_transaction_sha256 TEXT NOT NULL UNIQUE,
                signed_transaction_sha256 TEXT NOT NULL UNIQUE,
                exit_decision_id TEXT NOT NULL,
                signature TEXT NOT NULL,
                signed_transaction_base64 TEXT NOT NULL,
                status TEXT NOT NULL,
                rpc_error TEXT
            )
            """
        )
        row_status = status or execution["journal_status"]
        connection.execute(
            """
            INSERT INTO phase7_exit_settlement_submission_journal (
                request_sha256,
                final_settlement_transaction_sha256,
                signed_transaction_sha256,
                exit_decision_id,
                signature,
                signed_transaction_base64,
                status,
                rpc_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                execution["saved_request_sha256"],
                execution["final_settlement_transaction_sha256"],
                execution["signed_transaction_sha256"],
                execution["exit_decision_id"],
                signature,
                "signed-transaction",
                row_status,
                (
                    None
                    if row_status == "RPC_ACCEPTED"
                    else "network outcome unknown"
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _nested(status: str) -> dict:
    return {
        "signature": SIGNATURE,
        "status": status,
        "error": (
            "InstructionError(0, Custom(1))"
            if status == "FAILED"
            else None
        ),
        "commitment": "CONFIRMED",
        "search_transaction_history": True,
        "observation_only": True,
        "automatic_retry_performed": False,
        "transaction_submission_attempted": False,
    }


def _write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    chain_status: str = "PENDING",
    accepted: bool = True,
    execution_override: dict | None = None,
    journal_signature: str = SIGNATURE,
    journal_status: str | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        journal = root / "exit-journal.db"
        execution = _execution(journal, accepted=accepted)
        if execution_override:
            execution.update(execution_override)
        _write_journal(
            journal,
            execution,
            signature=journal_signature,
            status=journal_status,
        )
        execution_path = _write_json(
            root / "execution.json",
            execution,
        )

        observer = root / "phase7-exit-confirmation-observer"
        observer.write_bytes(b"reviewed-observer")
        observer.chmod(observer.stat().st_mode | stat.S_IXUSR)
        observer_sha = hashlib.sha256(observer.read_bytes()).hexdigest()

        monkeypatch.setattr(
            MODULE,
            "_load_execution_module",
            lambda source: _FakeExecutionModule,
        )
        observed = {}

        def fake_run_observer(*, observer_binary, signature, env):
            observed["observer"] = observer_binary
            observed["signature"] = signature
            observed["env"] = env
            return _nested(chain_status)

        monkeypatch.setattr(MODULE, "_run_observer", fake_run_observer)
        monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")
        monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
        monkeypatch.setenv("RPC_URL", "https://fallback.invalid")

        report = MODULE.build_post_execution_observation(
            source_tree=ROOT,
            saved_execution_path=execution_path,
            expected_execution_sha256=execution["execution_sha256"],
            observer_binary_path=observer,
            expected_observer_binary_sha256=observer_sha,
            rpc_url=RPC_URL,
            submission_journal_path=journal,
        )
        return report, observed


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["observation_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_pending_observation_requires_recheck_without_retry(monkeypatch):
    report, observed = _build(
        monkeypatch,
        chain_status="PENDING",
    )

    assert observed["signature"] == SIGNATURE
    assert MODULE.KEYPAIR_ENV not in observed["env"]
    assert MODULE.LIVE_SUBMIT_ENV not in observed["env"]
    assert "RPC_URL" not in observed["env"]
    assert observed["env"]["SOLANA_RPC_URL"] == RPC_URL
    assert report["journal_row_matches_execution"] is True
    assert report["destination_config_sha256"] == "e" * 64
    assert report["journal_unchanged"] is True
    assert report["confirmation_pending"] is True
    assert report["transaction_chain_confirmation_observed"] is False
    assert report["transaction_failure_observed"] is False
    assert report["confirmation_recheck_required"] is True
    assert report["terminal_settlement_receipt_required"] is False
    assert report["post_close_account_absence_proof_required"] is False
    assert report["automatic_resubmission_performed"] is False


def test_confirmed_observation_opens_closure_proof_only(monkeypatch):
    report, _ = _build(
        monkeypatch,
        chain_status="CONFIRMED",
    )

    assert report["confirmation_pending"] is False
    assert report["transaction_chain_confirmation_observed"] is True
    assert report["transaction_failure_observed"] is False
    assert report["confirmation_recheck_required"] is False
    assert report["terminal_settlement_receipt_required"] is True
    assert report["post_close_account_absence_proof_required"] is True
    assert report["post_exit_state_reconciliation_required"] is True
    assert report["failure_recovery_required"] is False
    assert report["live_capital_movement_confirmed"] is False


def test_failed_observation_requires_recovery_not_resubmission(monkeypatch):
    report, _ = _build(
        monkeypatch,
        chain_status="FAILED",
        accepted=False,
    )

    assert report["confirmation_pending"] is False
    assert report["transaction_chain_confirmation_observed"] is False
    assert report["transaction_failure_observed"] is True
    assert report["terminal_settlement_receipt_required"] is True
    assert report["post_close_account_absence_proof_required"] is False
    assert report["failure_recovery_required"] is True
    assert report["automatic_resubmission_performed"] is False
    assert report["observation_error"]


def test_uncertain_submission_can_later_be_observed_confirmed(monkeypatch):
    report, _ = _build(
        monkeypatch,
        chain_status="CONFIRMED",
        accepted=False,
    )

    assert report["execution_journal_status"] == "RPC_UNCERTAIN"
    assert report["execution_rpc_accepted"] is False
    assert report["transaction_chain_confirmation_observed"] is True
    assert report["failure_recovery_required"] is False


def test_journal_signature_mismatch_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="signature binding mismatch"):
        _build(
            monkeypatch,
            journal_signature=(
                "4NfR2yR4tXbFp7YqZ3wDqM7K4k8Qm9vY6YV7A7A7A7"
                "A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A"
            ),
        )


def test_journal_status_mismatch_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="status binding mismatch"):
        _build(
            monkeypatch,
            accepted=True,
            journal_status="RPC_UNCERTAIN",
        )


def test_rpc_endpoint_must_match_execution(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            execution_override={"rpc_endpoint_sha256": "f" * 64},
        )


def test_resealed_observation_cannot_claim_retry(monkeypatch):
    report, _ = _build(monkeypatch)
    report["automatic_resubmission_performed"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="automatic_resubmission_performed=false",
    ):
        MODULE.validate_post_execution_observation(report)


def test_resealed_pending_observation_cannot_claim_confirmation(monkeypatch):
    report, _ = _build(monkeypatch, chain_status="PENDING")
    report["transaction_chain_confirmation_observed"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="confirmed flag mismatch"):
        MODULE.validate_post_execution_observation(report)


def test_post_execution_tool_has_no_submission_or_signing_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'mode=ro' in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "while True" not in source
    assert "for attempt" not in source
    assert '"automatic_resubmission_performed": False' in source
    assert '"live_capital_movement_confirmed": False' in source
    assert '"phase7_promotion_persisted": False' in source
