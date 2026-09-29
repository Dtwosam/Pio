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
    / "check_phase7_controlled_live_exit_authorization_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_authorization_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _decision() -> dict:
    return {
        "verification_sha256": "a" * 64,
        "lifecycle_status_sha256": "b" * 64,
        "opened_decision_id": "11111111-2222-4333-8444-555555555555",
        "opening_signature": "sig-open",
        "pool_address": "11111111111111111111111111111111",
        "position_address": "33333333333333333333333333333333",
        "executor_wallet_pubkey": "44444444444444444444444444444444",
        "rpc_endpoint_sha256": "c" * 64,
        "executor_binary_sha256": "d" * 64,
        "position_snapshot_sha256": "e" * 64,
        "capture_slot_start": 100,
        "capture_slot_end": 101,
        "decision_record_id": "01234567-89ab-4def-8123-456789abcdef",
        "decider_principal": "wyck@example.com",
        "issued_at": "2026-09-29T10:00:00Z",
        "expires_at": "2026-09-29T10:05:00Z",
        "signature_verified": True,
        "human_exit_decision_verified": True,
        "exit_decision_present": True,
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


def _fresh() -> dict:
    return {
        "lifecycle_status_sha256": "f" * 64,
        "decision_id": _decision()["opened_decision_id"],
        "signature": "sig-open",
        "pool_address": _decision()["pool_address"],
        "position_address": _decision()["position_address"],
        "executor_wallet_pubkey": _decision()["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": "c" * 64,
        "executor_binary_sha256": "d" * 64,
        "position_snapshot_sha256": "0" * 64,
        "capture_slot_start": 102,
        "capture_slot_end": 103,
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


class _FakeDecisionModule:
    @staticmethod
    def validate_verification(value):
        assert isinstance(value, dict)


class _FakeLifecycleModule:
    @staticmethod
    def validate_open_position_lifecycle_status(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    decision_override: dict | None = None,
    fresh_override: dict | None = None,
    now: str = "2026-09-29T10:01:00Z",
) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        decision = _decision()
        fresh = _fresh()
        if decision_override:
            decision.update(decision_override)
        if fresh_override:
            fresh.update(fresh_override)
        decision_path = _write(root / "decision.json", decision)
        fresh_path = _write(root / "fresh.json", fresh)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeDecisionModule, _FakeLifecycleModule),
        )
        return MODULE.build_exit_authorization_readiness(
            source_tree=ROOT,
            saved_decision_verification_path=decision_path,
            expected_decision_verification_sha256=decision[
                "verification_sha256"
            ],
            fresh_lifecycle_status_path=fresh_path,
            expected_fresh_lifecycle_status_sha256=fresh[
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


def test_readiness_accepts_newer_snapshot_for_same_open_position(monkeypatch):
    report = _build(monkeypatch)

    assert report["original_capture_slot_end"] == 101
    assert report["fresh_capture_slot_start"] == 102
    assert report["original_position_snapshot_sha256"] == "e" * 64
    assert report["fresh_position_snapshot_sha256"] == "0" * 64
    assert report["fresh_snapshot_is_newer"] is True
    assert report["position_identity_matches"] is True
    assert report["position_still_open"] is True
    assert report["human_exit_decision_verified"] is True
    assert report["exit_authorization_readiness_ready"] is True
    assert report["exit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_expired_signed_decision_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="has expired"):
        _build(
            monkeypatch,
            now="2026-09-29T10:05:01Z",
        )


def test_fresh_snapshot_must_be_strictly_newer(monkeypatch):
    with pytest.raises(ValueError, match="not newer"):
        _build(
            monkeypatch,
            fresh_override={
                "capture_slot_start": 101,
                "capture_slot_end": 102,
            },
        )


def test_position_identity_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="position_address differs"):
        _build(
            monkeypatch,
            fresh_override={
                "position_address": "99999999999999999999999999999999",
            },
        )


def test_rpc_identity_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="rpc_endpoint_sha256 differs"):
        _build(
            monkeypatch,
            fresh_override={"rpc_endpoint_sha256": "9" * 64},
        )


def test_closed_position_cannot_be_authorization_ready(monkeypatch):
    with pytest.raises(ValueError, match="proves the position is closed"):
        _build(
            monkeypatch,
            fresh_override={"position_closed_proven": True},
        )


def test_missing_position_account_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="no longer observes"):
        _build(
            monkeypatch,
            fresh_override={"position_account_present": False},
        )


def test_signed_decision_cannot_pre_authorize_exit(monkeypatch):
    with pytest.raises(
        ValueError,
        match="signed EXIT decision verification unexpectedly authorizes",
    ):
        _build(
            monkeypatch,
            decision_override={"exit_authorized": True},
        )


def test_fresh_lifecycle_cannot_pre_authorize_submission(monkeypatch):
    with pytest.raises(
        ValueError,
        match="fresh open-position lifecycle unexpectedly authorizes",
    ):
        _build(
            monkeypatch,
            fresh_override={"transaction_submission_authorized": True},
        )


def test_resealed_readiness_cannot_authorize_exit(monkeypatch):
    report = _build(monkeypatch)
    report["exit_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="exit_authorized=false"):
        MODULE.validate_exit_authorization_readiness(report)


def test_resealed_readiness_cannot_claim_closed_position(monkeypatch):
    report = _build(monkeypatch)
    report["position_closed_proven"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="position_closed_proven=false"):
        MODULE.validate_exit_authorization_readiness(report)


def test_readiness_tool_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "controlled-live-submit" not in source
    assert "execution-confirmation" not in source
    assert "execution-recovery" not in source
    assert "verify-position-closed" not in source
    assert "load_executor_keypair" not in source
    assert "submit_execution_intent" not in source
    assert '"exit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
