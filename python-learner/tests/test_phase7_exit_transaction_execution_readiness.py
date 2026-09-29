from __future__ import annotations

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
    / "check_phase7_controlled_live_exit_transaction_execution_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_transaction_execution_readiness",
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


def _finalization() -> dict:
    return {
        "finalization_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "final_transaction_sha256": "b" * 64,
        "recent_blockhash": "11111111111111111111111111111111",
        "last_valid_block_height": 999,
        "exact_simulation_sha256": "c" * 64,
        "final_guard_sha256": "d" * 64,
        "final_wallet_authorization_sha256": "e" * 64,
        "finalizer_binary_sha256": "f" * 64,
        "decision_expires_at": "2026-09-29T10:05:00Z",
        "authorization_expires_at": "2026-09-29T10:03:30Z",
        "final_transaction_unsigned": True,
        "exact_simulation_succeeded": True,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
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


def _verification() -> dict:
    return {
        "verification_sha256": "1" * 64,
        "finalization_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "final_transaction_sha256": "b" * 64,
        "recent_blockhash": "11111111111111111111111111111111",
        "last_valid_block_height": 999,
        "exact_simulation_sha256": "c" * 64,
        "final_guard_sha256": "d" * 64,
        "final_wallet_authorization_sha256": "e" * 64,
        "finalizer_binary_sha256": "f" * 64,
        "decision_expires_at": "2026-09-29T10:05:00Z",
        "preparation_authorization_expires_at": "2026-09-29T10:03:30Z",
        "expires_at": "2026-09-29T10:03:10Z",
        "human_exact_exit_transaction_authorization_verified": True,
        "signature_verified": True,
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


class _FakeFinalization:
    @staticmethod
    def validate_exit_transaction_finalization(value):
        assert isinstance(value, dict)


class _FakeSigner:
    fresh = None

    @staticmethod
    def validate_verification(value):
        assert isinstance(value, dict)

    @classmethod
    def verify_authorization(cls, **kwargs):
        assert cls.fresh is not None
        return cls.fresh


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    finalization_override: dict | None = None,
    verification_override: dict | None = None,
    fresh_override: dict | None = None,
    block_height: int = 990,
    now: str = "2026-09-29T10:02:30Z",
) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finalization = _finalization()
        saved = _verification()
        if finalization_override:
            finalization.update(finalization_override)
        if verification_override:
            saved.update(verification_override)
        fresh = dict(saved)
        if fresh_override:
            fresh.update(fresh_override)

        finalization_path = _write(
            root / "finalization.json",
            finalization,
        )
        saved_verification_path = _write(
            root / "verification.json",
            saved,
        )
        for name in (
            "request.json",
            "payload.json",
            "signature",
            "allowed_signers",
        ):
            (root / name).write_text("fixture", encoding="utf-8")

        _FakeSigner.fresh = fresh
        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeFinalization, _FakeSigner),
        )
        monkeypatch.setattr(
            MODULE,
            "_rpc_block_height",
            lambda rpc_url: block_height,
        )

        return MODULE.build_exit_transaction_execution_readiness(
            source_tree=ROOT,
            saved_finalization_path=finalization_path,
            expected_finalization_sha256=finalization[
                "finalization_sha256"
            ],
            saved_authorization_verification_path=(
                saved_verification_path
            ),
            expected_saved_authorization_verification_sha256=saved[
                "verification_sha256"
            ],
            authorization_request_path=root / "request.json",
            authorization_payload_path=root / "payload.json",
            authorization_signature_path=root / "signature",
            authorization_allowed_signers_path=root / "allowed_signers",
            expected_authorization_allowed_signers_sha256="9" * 64,
            rpc_url=RPC_URL,
            now=now,
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_exit_execution_readiness_is_read_only_and_bound(monkeypatch):
    report = _build(monkeypatch)

    assert report["saved_finalization_sha256"] == "a" * 64
    assert report["saved_authorization_verification_sha256"] == "1" * 64
    assert report["fresh_authorization_verification_sha256"] == "1" * 64
    assert report["current_block_height"] == 990
    assert report["last_valid_block_height"] == 999
    assert report["block_height_remaining"] == 9
    assert report["blockhash_not_expired"] is True
    assert report["human_exact_exit_transaction_authorization_verified"] is True
    assert report["exit_transaction_execution_readiness_ready"] is True
    assert report["requires_keypair_identity_admission"] is True
    assert report["readiness_only"] is True
    assert report["exit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_last_valid_block_height_is_still_accepted(monkeypatch):
    report = _build(monkeypatch, block_height=999)

    assert report["block_height_remaining"] == 0
    assert report["blockhash_not_expired"] is True


def test_expired_blockhash_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="blockhash has expired"):
        _build(monkeypatch, block_height=1000)


def test_fresh_authorization_must_match_saved(monkeypatch):
    with pytest.raises(ValueError, match="differs from saved"):
        _build(
            monkeypatch,
            fresh_override={"verification_sha256": "2" * 64},
        )


def test_expired_exact_transaction_authorization_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="transaction authorization has expired",
    ):
        _build(
            monkeypatch,
            now="2026-09-29T10:03:11Z",
        )


def test_rpc_endpoint_must_match_finalization(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            finalization_override={
                "rpc_endpoint_sha256": "0" * 64
            },
        )


def test_finalized_transaction_must_remain_unsigned(monkeypatch):
    with pytest.raises(ValueError, match="no longer unsigned"):
        _build(
            monkeypatch,
            finalization_override={"final_transaction_unsigned": False},
        )


def test_authorization_verification_cannot_pre_authorize_submission(monkeypatch):
    with pytest.raises(
        ValueError,
        match="unexpectedly authorizes transaction_submission_authorized",
    ):
        _build(
            monkeypatch,
            verification_override={
                "transaction_submission_authorized": True
            },
            fresh_override={
                "transaction_submission_authorized": True
            },
        )


def test_resealed_readiness_cannot_authorize_signing(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_signing_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_signing_authorized=false",
    ):
        MODULE.validate_exit_transaction_execution_readiness(report)


def test_readiness_tool_has_no_keypair_or_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert "subprocess" not in source
    assert "load_executor_keypair" not in source
    assert "controlled-live-submit" not in source
    assert "send_transaction" not in source
    assert "execution_store" not in source
    assert '"exit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
