from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase7_controlled_live_exit_transaction_execution_admission.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_transaction_execution_admission",
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


def _readiness(
    *,
    sha: str = "a" * 64,
    current_height: int = 990,
) -> dict:
    return {
        "readiness_sha256": sha,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "final_transaction_sha256": "b" * 64,
        "recent_blockhash": "11111111111111111111111111111111",
        "last_valid_block_height": 999,
        "current_block_height": current_height,
        "block_height_remaining": 999 - current_height,
        "rpc_endpoint_sha256": hashlib.sha256(
            RPC_URL.encode("utf-8")
        ).hexdigest(),
        "human_exact_exit_transaction_authorization_verified": True,
        "exact_transaction_authorization_not_expired": True,
        "preparation_authorization_not_expired": True,
        "decision_not_expired": True,
        "final_transaction_unsigned": True,
        "final_exact_simulation_succeeded": True,
        "exit_transaction_execution_readiness_ready": True,
        "requires_keypair_identity_admission": True,
        "requires_immediate_execution_after_readiness": True,
        "requires_fresh_blockhash_recheck_at_execution": True,
        "requires_single_submission_only": True,
        "exit_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }


class _FakeReadinessModule:
    fresh = None

    @staticmethod
    def validate_exit_transaction_execution_readiness(value):
        assert isinstance(value, dict)

    @classmethod
    def build_exit_transaction_execution_readiness(cls, **kwargs):
        assert cls.fresh is not None
        return cls.fresh


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    saved_override: dict | None = None,
    fresh_override: dict | None = None,
    wallet_pubkey: str = WALLET,
    live_submit_opt_in: bool = True,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        saved = _readiness()
        if saved_override:
            saved.update(saved_override)
        fresh = dict(saved)
        fresh["readiness_sha256"] = "c" * 64
        fresh["current_block_height"] = saved["current_block_height"] + 1
        fresh["block_height_remaining"] = (
            fresh["last_valid_block_height"]
            - fresh["current_block_height"]
        )
        if fresh_override:
            fresh.update(fresh_override)

        saved_path = _write(root / "readiness.json", saved)
        for name in (
            "finalization.json",
            "verification.json",
            "request.json",
            "payload.json",
            "signature",
            "allowed_signers",
        ):
            (root / name).write_text("fixture", encoding="utf-8")

        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        keypair = root / "executor-keypair.json"
        keypair.write_text("[1,2,3]", encoding="utf-8")
        if os.name == "posix":
            keypair.chmod(0o600)

        if live_submit_opt_in:
            monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
        else:
            monkeypatch.delenv(MODULE.LIVE_SUBMIT_ENV, raising=False)
        monkeypatch.setenv(MODULE.KEYPAIR_ENV, str(keypair))
        monkeypatch.setattr(
            MODULE,
            "_load_readiness_module",
            lambda source: _FakeReadinessModule,
        )
        _FakeReadinessModule.fresh = fresh

        observed = {}

        def fake_wallet_status(*, executor_binary, keypair_path):
            observed["binary"] = executor_binary
            observed["keypair_path"] = keypair_path
            return {
                "pubkey": wallet_pubkey,
                "keypair_source": MODULE.KEYPAIR_ENV,
                "permissions_verified": True,
            }

        monkeypatch.setattr(MODULE, "_wallet_status", fake_wallet_status)

        report = MODULE.build_exit_transaction_execution_admission(
            source_tree=ROOT,
            saved_readiness_path=saved_path,
            expected_saved_readiness_sha256=saved["readiness_sha256"],
            saved_finalization_path=root / "finalization.json",
            expected_finalization_sha256="d" * 64,
            saved_authorization_verification_path=root / "verification.json",
            expected_saved_authorization_verification_sha256="e" * 64,
            authorization_request_path=root / "request.json",
            authorization_payload_path=root / "payload.json",
            authorization_signature_path=root / "signature",
            authorization_allowed_signers_path=root / "allowed_signers",
            expected_authorization_allowed_signers_sha256="f" * 64,
            rpc_url=RPC_URL,
            executor_binary_path=binary,
            expected_executor_binary_sha256=binary_sha,
            now="2026-09-29T10:02:40Z",
        )
        return report, observed


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["admission_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_exit_execution_admission_verifies_identity_without_authorizing(monkeypatch):
    report, observed = _build(monkeypatch)

    assert observed["binary"].name == "meteora-executor"
    assert observed["keypair_path"].is_absolute()
    assert report["fresh_current_block_height"] == 991
    assert report["fresh_block_height_remaining"] == 8
    assert report["block_height_monotonic"] is True
    assert report["blockhash_not_expired"] is True
    assert report["runtime_live_submit_opt_in_present"] is True
    assert report["executor_keypair_identity_verified"] is True
    assert report["wrapper_read_private_key_bytes"] is False
    assert report["exit_transaction_execution_admission_ready"] is True
    assert report["requires_separate_single_shot_submitter"] is True
    assert report["exit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_live_submit_opt_in_is_required_but_not_execution(monkeypatch):
    with pytest.raises(ValueError, match="required for EXIT execution admission"):
        _build(monkeypatch, live_submit_opt_in=False)


def test_wallet_identity_mismatch_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="wallet differs"):
        _build(
            monkeypatch,
            wallet_pubkey="77777777777777777777777777777777",
        )


def test_fresh_readiness_static_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="static binding differs"):
        _build(
            monkeypatch,
            fresh_override={
                "position_address": "77777777777777777777777777777777"
            },
        )


def test_block_height_regression_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="block height regressed"):
        _build(
            monkeypatch,
            fresh_override={
                "current_block_height": 989,
                "block_height_remaining": 10,
            },
        )


def test_executor_binary_trust_root_mismatch_fails_closed(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

        with pytest.raises(ValueError, match="external trust root"):
            MODULE._verify_executor_binary(
                executor_binary_path=binary,
                expected_sha256="0" * 64,
            )


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions test")
def test_broad_keypair_permissions_fail_closed(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        keypair = Path(tmp) / "keypair.json"
        keypair.write_text("[1,2,3]", encoding="utf-8")
        keypair.chmod(0o640)

        with pytest.raises(ValueError, match="permissions are too broad"):
            MODULE._validate_keypair_path(str(keypair))


def test_resealed_admission_cannot_authorize_signing(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_signing_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_signing_authorized=false",
    ):
        MODULE.validate_exit_transaction_execution_admission(report)


def test_admission_only_invokes_wallet_status_not_submission():
    source = TOOL.read_text(encoding="utf-8")

    assert 'WALLET_STATUS_COMMAND = "wallet-status"' in source
    assert "load_executor_keypair" not in source
    assert "sign_prepared_transaction" not in source
    assert "controlled-live-submit" not in source
    assert "send_transaction" not in source
    assert "record_sent(" not in source
    assert '"wrapper_read_private_key_bytes": False' in source
    assert '"exit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
