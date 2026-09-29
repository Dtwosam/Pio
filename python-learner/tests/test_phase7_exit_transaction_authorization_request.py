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
    / "build_phase7_controlled_live_exit_transaction_authorization_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_transaction_authorization_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _FakeFinalization:
    @staticmethod
    def validate_exit_transaction_finalization(value):
        assert isinstance(value, dict)


def _finalization() -> dict:
    return {
        "finalization_sha256": "a" * 64,
        "saved_preparation_sha256": "b" * 64,
        "saved_readiness_sha256": "c" * 64,
        "opened_decision_id": "11111111-2222-4333-8444-555555555555",
        "opening_signature": "sig-open",
        "exit_decision_id": "01234567-89ab-4def-8123-456789abcdef",
        "pool_address": "11111111111111111111111111111111",
        "position_address": "33333333333333333333333333333333",
        "executor_wallet_pubkey": "44444444444444444444444444444444",
        "user_token_x": "55555555555555555555555555555555",
        "user_token_y": "66666666666666666666666666666666",
        "final_transaction_sha256": "d" * 64,
        "recent_blockhash": "11111111111111111111111111111111",
        "last_valid_block_height": 999,
        "prepared_rpc_context_slot": 110,
        "exact_simulation_sha256": "e" * 64,
        "exact_simulation_rpc_context_slot": 111,
        "final_guard_sha256": "f" * 64,
        "final_wallet_authorization_sha256": "0" * 64,
        "finalizer_binary_sha256": "1" * 64,
        "decision_expires_at": "2026-09-29T10:05:00Z",
        "authorization_expires_at": "2026-09-29T10:03:30Z",
        "finalized_at": "2026-09-29T10:02:00Z",
        "authorization_not_expired": True,
        "decision_not_expired": True,
        "final_risk_accepted": True,
        "final_transaction_guard_accepted": True,
        "final_transaction_unsigned": True,
        "expected_wallet_verified": True,
        "exact_simulation_succeeded": True,
        "exit_transaction_finalized": True,
        "exact_exit_transaction_authorization_required": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "separate_exit_execution_gate_required": True,
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


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    override: dict | None = None,
    now: str = "2026-09-29T10:02:10Z",
) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finalization = _finalization()
        if override:
            finalization.update(override)
        path = _write(root / "finalization.json", finalization)

        monkeypatch.setattr(
            MODULE,
            "_load_finalization_module",
            lambda source: _FakeFinalization,
        )
        return MODULE.build_exit_transaction_authorization_request(
            source_tree=ROOT,
            saved_finalization_path=path,
            expected_finalization_sha256=finalization[
                "finalization_sha256"
            ],
            now=now,
        )


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_finalization_dependency_is_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_exact_exit_transaction_request_is_bound_and_non_authorizing(monkeypatch):
    request = _build(monkeypatch)

    assert request["finalization_sha256"] == "a" * 64
    assert request["preparation_sha256"] == "b" * 64
    assert request["readiness_sha256"] == "c" * 64
    assert request["final_transaction_sha256"] == "d" * 64
    assert request["last_valid_block_height"] == 999
    assert request["transaction_finalization_verified"] is True
    assert request["transaction_unsigned"] is True
    assert request["exact_simulation_verified"] is True
    assert (
        request["explicit_human_exact_transaction_authorization_required"]
        is True
    )
    assert request["single_submission_only_required"] is True
    assert request["exact_transaction_authorization_present"] is False
    assert request["exit_authorized"] is False
    assert request["transaction_signing_authorized"] is False
    assert request["transaction_submission_authorized"] is False


def test_expired_preparation_authorization_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="preparation authorization has expired",
    ):
        _build(
            monkeypatch,
            now="2026-09-29T10:03:31Z",
        )


def test_request_cannot_predate_finalization(monkeypatch):
    with pytest.raises(ValueError, match="predates finalization"):
        _build(
            monkeypatch,
            now="2026-09-29T10:01:00Z",
        )


def test_finalization_cannot_pre_authorize_exit(monkeypatch):
    with pytest.raises(
        ValueError,
        match="unexpectedly authorizes exit_authorized",
    ):
        _build(
            monkeypatch,
            override={"exit_authorized": True},
        )


def test_resealed_request_cannot_claim_authorization_present(monkeypatch):
    request = _build(monkeypatch)
    request["exact_transaction_authorization_present"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="exact_transaction_authorization_present=false",
    ):
        MODULE.validate_exit_transaction_authorization_request(request)


def test_resealed_request_cannot_authorize_signing(monkeypatch):
    request = _build(monkeypatch)
    request["transaction_signing_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="transaction_signing_authorized=false",
    ):
        MODULE.validate_exit_transaction_authorization_request(request)


def test_resealed_request_cannot_authorize_submission(monkeypatch):
    request = _build(monkeypatch)
    request["transaction_submission_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_transaction_authorization_request(request)


def test_request_tool_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "load_executor_keypair" not in source
    assert "controlled-live-submit" not in source
    assert "send_transaction" not in source
    assert "execution_store" not in source
    assert '"exit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
