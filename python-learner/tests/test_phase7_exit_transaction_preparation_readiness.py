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
    / "check_phase7_controlled_live_exit_transaction_preparation_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_transaction_preparation_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


OPENED_DECISION_ID = "11111111-2222-4333-8444-555555555555"
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"
WALLET = "44444444444444444444444444444444"


def _authorization() -> dict:
    return {
        "verification_sha256": "a" * 64,
        "fresh_lifecycle_status_sha256": "b" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "opening_signature": "sig-open",
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": "1" * 64,
        "executor_binary_sha256": "2" * 64,
        "fresh_position_snapshot_sha256": "3" * 64,
        "fresh_capture_slot_start": 102,
        "fresh_capture_slot_end": 103,
        "decision_record_id": "01234567-89ab-4def-8123-456789abcdef",
        "decider_principal": "decider@example.com",
        "decision_expires_at": "2026-09-29T10:05:00Z",
        "approval_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "approver_principal": "approver@example.com",
        "issued_at": "2026-09-29T10:01:30Z",
        "expires_at": "2026-09-29T10:03:30Z",
        "signature_verified": True,
        "authorization_within_decision_expiry": True,
        "human_exit_authorization_verified": True,
        "exit_authorization_present": True,
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


def _lifecycle() -> dict:
    return {
        "lifecycle_status_sha256": "c" * 64,
        "decision_id": OPENED_DECISION_ID,
        "signature": "sig-open",
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": "1" * 64,
        "executor_binary_sha256": "2" * 64,
        "position_snapshot_sha256": "4" * 64,
        "capture_slot_start": 104,
        "capture_slot_end": 105,
        "lifecycle_route": "OPEN_POSITION_OBSERVATION",
        "position_account_present": True,
        "position_closed_proven": False,
        "open_position_snapshot_ready": True,
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


class _FakeAuthorization:
    @staticmethod
    def validate_verification(value):
        assert isinstance(value, dict)


class _FakeLifecycle:
    @staticmethod
    def validate_open_position_lifecycle_status(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    authorization_override: dict | None = None,
    lifecycle_override: dict | None = None,
    now: str = "2026-09-29T10:02:00Z",
) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        authorization = _authorization()
        lifecycle = _lifecycle()
        if authorization_override:
            authorization.update(authorization_override)
        if lifecycle_override:
            lifecycle.update(lifecycle_override)
        authorization_path = _write(
            root / "authorization.json",
            authorization,
        )
        lifecycle_path = _write(root / "lifecycle.json", lifecycle)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeAuthorization, _FakeLifecycle),
        )
        return MODULE.build_exit_transaction_preparation_readiness(
            source_tree=ROOT,
            saved_authorization_verification_path=authorization_path,
            expected_authorization_verification_sha256=authorization[
                "verification_sha256"
            ],
            fresh_lifecycle_status_path=lifecycle_path,
            expected_fresh_lifecycle_status_sha256=lifecycle[
                "lifecycle_status_sha256"
            ],
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


def test_preparation_readiness_is_bound_and_non_executing(monkeypatch):
    report = _build(monkeypatch)

    assert report["opened_decision_id"] == OPENED_DECISION_ID
    assert report["position_address"] == POSITION
    assert report["pool_address"] == POOL
    assert report["authorized_capture_slot_end"] == 103
    assert report["fresh_capture_slot_start"] == 104
    assert report["authorization_signature_verified"] is True
    assert report["human_exit_authorization_verified"] is True
    assert report["fresh_snapshot_is_newer"] is True
    assert report["position_still_open"] is True
    assert report["exit_transaction_preparation_readiness_ready"] is True
    assert report["unsigned_exit_transaction_construction_required"] is True
    assert report["fresh_simulation_required"] is True
    assert report["exit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_expired_authorization_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="authorization has expired"):
        _build(
            monkeypatch,
            now="2026-09-29T10:03:31Z",
        )


def test_stale_lifecycle_snapshot_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="not newer"):
        _build(
            monkeypatch,
            lifecycle_override={
                "capture_slot_start": 103,
                "capture_slot_end": 104,
            },
        )


def test_position_identity_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="position_address differs"):
        _build(
            monkeypatch,
            lifecycle_override={
                "position_address": "99999999999999999999999999999999"
            },
        )


def test_closed_position_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="proves the position is closed"):
        _build(
            monkeypatch,
            lifecycle_override={"position_closed_proven": True},
        )


def test_authorization_cannot_pre_authorize_exit_execution(monkeypatch):
    with pytest.raises(
        ValueError,
        match="unexpectedly authorizes exit_authorized",
    ):
        _build(
            monkeypatch,
            authorization_override={"exit_authorized": True},
        )


def test_resealed_readiness_cannot_authorize_exit(monkeypatch):
    report = _build(monkeypatch)
    report["exit_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="exit_authorized=false"):
        MODULE.validate_exit_transaction_preparation_readiness(report)


def test_resealed_readiness_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_transaction_preparation_readiness(report)


def test_preparation_readiness_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "build-token-exit-from-chain" not in source
    assert "controlled-live-submit" not in source
    assert "execution-confirmation" not in source
    assert "execution-recovery" not in source
    assert "verify-position-closed" not in source
    assert "PIO_LIVE_SUBMIT_ENABLED" not in source
    assert '"exit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
