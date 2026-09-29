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
    / "build_phase7_controlled_live_exit_decision_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_decision_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _lifecycle() -> dict:
    return {
        "lifecycle_status_sha256": "a" * 64,
        "decision_id": "11111111-2222-4333-8444-555555555555",
        "signature": "sig-open",
        "pool_address": "11111111111111111111111111111111",
        "position_address": "33333333333333333333333333333333",
        "executor_wallet_pubkey": "44444444444444444444444444444444",
        "rpc_endpoint_sha256": "b" * 64,
        "executor_binary_path": "/opt/pio/bin/meteora-executor",
        "executor_binary_sha256": "c" * 64,
        "position_snapshot_sha256": "d" * 64,
        "capture_slot_start": 100,
        "capture_slot_end": 101,
        "lower_bin_id": -10,
        "upper_bin_id": 10,
        "total_x_amount": "1000",
        "total_y_amount": "2000",
        "fee_x": "5",
        "fee_y": "6",
        "reward_one": "7",
        "reward_two": "8",
        "bin_count": 2,
        "position_account_present": True,
        "position_closed_proven": False,
        "open_position_snapshot_ready": True,
        "lifecycle_route": "OPEN_POSITION_OBSERVATION",
        "requires_separate_exit_decision": True,
        "new_live_entry_authorized": False,
        "exit_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
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


def _build(monkeypatch, override: dict | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lifecycle = _lifecycle()
        if override:
            lifecycle.update(override)
        path = _write(root / "lifecycle.json", lifecycle)

        class FakeLifecycle:
            @staticmethod
            def validate_open_position_lifecycle_status(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_lifecycle_module",
            lambda source: FakeLifecycle,
        )
        return MODULE.build_exit_decision_request(
            source_tree=ROOT,
            saved_lifecycle_status_path=path,
            expected_lifecycle_status_sha256=lifecycle[
                "lifecycle_status_sha256"
            ],
        )


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_lifecycle_dependency_is_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_exit_decision_request_is_bound_and_non_authorizing(monkeypatch):
    request = _build(monkeypatch)

    assert request["opened_decision_id"] == _lifecycle()["decision_id"]
    assert request["opening_signature"] == "sig-open"
    assert request["position_address"] == _lifecycle()["position_address"]
    assert request["pool_address"] == _lifecycle()["pool_address"]
    assert request["exit_decision_request_ready"] is True
    assert request["explicit_human_exit_decision_required"] is True
    assert request["fresh_lifecycle_recheck_required"] is True
    assert request["exit_decision_present"] is False
    assert request["exit_authorized"] is False
    assert request["transaction_signing_authorized"] is False
    assert request["transaction_submission_authorized"] is False
    assert request["new_live_capital_authorized"] is False


def test_lifecycle_digest_must_match(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lifecycle = _lifecycle()
        path = _write(root / "lifecycle.json", lifecycle)

        class FakeLifecycle:
            @staticmethod
            def validate_open_position_lifecycle_status(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_lifecycle_module",
            lambda source: FakeLifecycle,
        )
        with pytest.raises(ValueError, match="lifecycle digest mismatch"):
            MODULE.build_exit_decision_request(
                source_tree=ROOT,
                saved_lifecycle_status_path=path,
                expected_lifecycle_status_sha256="f" * 64,
            )


def test_closed_position_cannot_get_exit_decision_request(monkeypatch):
    with pytest.raises(ValueError, match="cannot target a closed position"):
        _build(monkeypatch, {"position_closed_proven": True})


def test_lifecycle_must_preserve_separate_exit_decision_boundary(monkeypatch):
    with pytest.raises(ValueError, match="lost the separate EXIT-decision"):
        _build(monkeypatch, {"requires_separate_exit_decision": False})


def test_lifecycle_cannot_pre_authorize_exit(monkeypatch):
    with pytest.raises(
        ValueError,
        match="unexpectedly authorizes exit_authorized",
    ):
        _build(monkeypatch, {"exit_authorized": True})


def test_resealed_request_cannot_claim_exit_decision(monkeypatch):
    request = _build(monkeypatch)
    request["exit_decision_present"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="exit_decision_present=false"):
        MODULE.validate_exit_decision_request(request)


def test_resealed_request_cannot_authorize_exit(monkeypatch):
    request = _build(monkeypatch)
    request["exit_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="exit_authorized=false"):
        MODULE.validate_exit_decision_request(request)


def test_resealed_request_cannot_authorize_submission(monkeypatch):
    request = _build(monkeypatch)
    request["transaction_submission_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_decision_request(request)


def test_exit_decision_request_tool_has_no_execution_path():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "controlled-live-submit" not in source
    assert "execution-confirmation" not in source
    assert "execution-recovery" not in source
    assert "verify-position-closed" not in source
    assert '"exit_decision_present": False' in source
    assert '"exit_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
