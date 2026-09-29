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
    / "build_phase7_controlled_live_exit_authorization_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_authorization_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _readiness() -> dict:
    return {
        "readiness_sha256": "a" * 64,
        "saved_decision_verification_sha256": "b" * 64,
        "original_lifecycle_status_sha256": "c" * 64,
        "saved_fresh_lifecycle_status_sha256": "d" * 64,
        "opened_decision_id": "11111111-2222-4333-8444-555555555555",
        "opening_signature": "sig-open",
        "pool_address": "11111111111111111111111111111111",
        "position_address": "33333333333333333333333333333333",
        "executor_wallet_pubkey": "44444444444444444444444444444444",
        "rpc_endpoint_sha256": "e" * 64,
        "executor_binary_sha256": "f" * 64,
        "fresh_position_snapshot_sha256": "0" * 64,
        "fresh_capture_slot_start": 102,
        "fresh_capture_slot_end": 103,
        "decision_record_id": "01234567-89ab-4def-8123-456789abcdef",
        "decider_principal": "wyck@example.com",
        "decision_issued_at": "2026-09-29T10:00:00Z",
        "decision_expires_at": "2026-09-29T10:05:00Z",
        "decision_signature_verified": True,
        "decision_not_expired": True,
        "fresh_lifecycle_valid": True,
        "fresh_snapshot_is_newer": True,
        "position_identity_matches": True,
        "position_account_present": True,
        "position_closed_proven": False,
        "position_still_open": True,
        "exit_decision_present": True,
        "human_exit_decision_verified": True,
        "exit_authorization_readiness_ready": True,
        "explicit_human_exit_authorization_required": True,
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
    @staticmethod
    def validate_exit_authorization_readiness(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    override: dict | None = None,
    now: str = "2026-09-29T10:01:00Z",
) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        readiness = _readiness()
        if override:
            readiness.update(override)
        path = _write(root / "readiness.json", readiness)

        monkeypatch.setattr(
            MODULE,
            "_load_readiness_module",
            lambda source: _FakeReadinessModule,
        )
        return MODULE.build_exit_authorization_request(
            source_tree=ROOT,
            saved_readiness_path=path,
            expected_readiness_sha256=readiness["readiness_sha256"],
            now=now,
        )


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_readiness_dependency_is_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_exit_authorization_request_is_bound_and_non_authorizing(monkeypatch):
    request = _build(monkeypatch)

    assert request["exit_authorization_readiness_sha256"] == "a" * 64
    assert request["decision_verification_sha256"] == "b" * 64
    assert request["fresh_lifecycle_status_sha256"] == "d" * 64
    assert request["position_address"] == _readiness()["position_address"]
    assert request["fresh_capture_slot_start"] == 102
    assert request["human_exit_decision_verified"] is True
    assert request["position_still_open"] is True
    assert request["exit_authorization_request_ready"] is True
    assert request["explicit_human_exit_authorization_required"] is True
    assert request["authorization_must_not_outlive_decision"] is True
    assert request["exit_authorization_present"] is False
    assert request["exit_authorized"] is False
    assert request["transaction_signing_authorized"] is False
    assert request["transaction_submission_authorized"] is False


def test_expired_exit_decision_cannot_create_authorization_request(monkeypatch):
    with pytest.raises(ValueError, match="has expired"):
        _build(
            monkeypatch,
            now="2026-09-29T10:05:01Z",
        )


def test_closed_position_readiness_cannot_create_request(monkeypatch):
    with pytest.raises(ValueError, match="cannot target a closed position"):
        _build(
            monkeypatch,
            override={"position_closed_proven": True},
        )


def test_missing_verified_decision_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="lost human_exit_decision_verified",
    ):
        _build(
            monkeypatch,
            override={"human_exit_decision_verified": False},
        )


def test_not_ready_input_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="lost exit_authorization_readiness_ready",
    ):
        _build(
            monkeypatch,
            override={"exit_authorization_readiness_ready": False},
        )


def test_readiness_cannot_pre_authorize_exit(monkeypatch):
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
    request["exit_authorization_present"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="exit_authorization_present=false"):
        MODULE.validate_exit_authorization_request(request)


def test_resealed_request_cannot_authorize_exit(monkeypatch):
    request = _build(monkeypatch)
    request["exit_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="exit_authorized=false"):
        MODULE.validate_exit_authorization_request(request)


def test_resealed_request_cannot_authorize_submission(monkeypatch):
    request = _build(monkeypatch)
    request["transaction_submission_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_authorization_request(request)


def test_authorization_request_tool_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "controlled-live-submit" not in source
    assert "execution-confirmation" not in source
    assert "execution-recovery" not in source
    assert "verify-position-closed" not in source
    assert "load_executor_keypair" not in source
    assert "submit_execution_intent" not in source
    assert '"exit_authorization_present": False' in source
    assert '"exit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
