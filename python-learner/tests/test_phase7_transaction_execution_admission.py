from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase7_controlled_live_transaction_execution_admission.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_transaction_execution_admission",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _readiness(*, height: int = 100, sha: str = "a" * 64) -> dict:
    return {
        "readiness_sha256": sha,
        "transaction_execution_readiness_ready": True,
        "readiness_only": True,
        "decision_id": "11111111-2222-4333-8444-555555555555",
        "pool_address": "11111111111111111111111111111111",
        "executor_wallet_pubkey": "22222222222222222222222222222222",
        "prepared_transaction_sha256": "1" * 64,
        "final_simulation_sha256": "2" * 64,
        "execution_intent_snapshot_sha256": "3" * 64,
        "prepared_last_valid_block_height": 120,
        "current_block_height": height,
        "block_height_remaining": 120 - height,
        "blockhash_not_expired": True,
        "rpc_endpoint_sha256": "4" * 64,
        "executor_binary_path": "/opt/pio/bin/meteora-executor",
        "executor_binary_sha256": "5" * 64,
        "expected_executor_binary_sha256": "5" * 64,
        "executor_binary_hash_matches_expected": True,
        "executor_binary_hash_trust_external": True,
        "live_submit_feature_verified": True,
        "runtime_live_submit_opt_in_present": True,
        "fresh_presubmit_matches_saved": True,
        "fresh_request_matches_saved": True,
        "fresh_transaction_authorization_matches_saved": True,
        "human_transaction_execution_authorization_verified": True,
        "transaction_authorization_not_expired": True,
        "execution_intent_still_unsigned": True,
        "execution_intent_still_error_free": True,
        "final_simulation_still_succeeded": True,
        "requires_immediate_execution_after_readiness": True,
        "requires_executor_native_blockhash_recheck": True,
        "requires_single_submission_only": True,
        "requires_confirmation_receipt_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
        "production_execution_database_modified": False,
    }


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _args(root: Path) -> dict:
    return {
        "repository": root,
        "source_tree": ROOT,
        "phase6_post_promotion_audit_path": root / "phase6.json",
        "saved_phase7_evidence_status_path": root / "status.json",
        "saved_phase7_evidence_plan_path": root / "plan.json",
        "saved_input_preflight_path": root / "preflight.json",
        "controlled_live_config_path": root / "controlled.json",
        "proposal_path": root / "proposal.json",
        "executor_wallet_pubkey": "22222222222222222222222222222222",
        "saved_controlled_live_authorization_verification_path": (
            root / "controlled-verification.json"
        ),
        "controlled_live_signed_payload_path": root / "controlled-payload.json",
        "controlled_live_signature_path": root / "controlled.sig",
        "controlled_live_allowed_signers_path": root / "controlled-allowed",
        "expected_controlled_live_allowed_signers_sha256": "6" * 64,
        "saved_authorization_readiness_path": root / "auth-readiness.json",
        "expected_authorization_readiness_sha256": "7" * 64,
        "execution_database_path": root / "execution.db",
        "saved_presubmit_gate_path": root / "presubmit.json",
        "transaction_request_path": root / "request.json",
        "saved_transaction_authorization_verification_path": (
            root / "transaction-verification.json"
        ),
        "transaction_signed_payload_path": root / "transaction-payload.json",
        "transaction_signature_path": root / "transaction.sig",
        "transaction_allowed_signers_path": root / "transaction-allowed",
        "expected_transaction_allowed_signers_sha256": "8" * 64,
        "executor_binary_path": "/opt/pio/bin/meteora-executor",
        "expected_executor_binary_sha256": "5" * 64,
        "rpc_url": "https://rpc.example.invalid",
        "now": "2026-09-29T08:00:00Z",
    }


def _build(
    monkeypatch,
    *,
    saved: dict | None = None,
    fresh: dict | None = None,
    wallet_pubkey: str = "22222222222222222222222222222222",
) -> dict:
    saved_value = copy.deepcopy(saved if saved is not None else _readiness())
    fresh_value = copy.deepcopy(
        fresh if fresh is not None else _readiness(height=101, sha="b" * 64)
    )

    class FakeReadiness:
        @staticmethod
        def validate_transaction_execution_readiness(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_transaction_execution_readiness(**kwargs):
            return copy.deepcopy(fresh_value)

    monkeypatch.setattr(
        MODULE,
        "_load_readiness_module",
        lambda source: FakeReadiness,
    )
    monkeypatch.setattr(
        MODULE,
        "_wallet_status",
        lambda **kwargs: {
            "pubkey": wallet_pubkey,
            "keypair_source": MODULE.KEYPAIR_ENV,
        },
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        keypair = root / "executor-keypair.json"
        keypair.write_text("[1,2,3]", encoding="utf-8")
        if os.name == "posix":
            keypair.chmod(0o600)

        saved_path = _write(root, "saved-readiness.json", saved_value)
        monkeypatch.setenv(MODULE.KEYPAIR_ENV, str(keypair))
        monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")

        return MODULE.build_execution_admission(
            saved_transaction_execution_readiness_path=saved_path,
            expected_saved_readiness_sha256=saved_value["readiness_sha256"],
            **_args(root),
        )


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


def test_static_readiness_binding_ignores_only_height_and_digest():
    saved = _readiness(height=100, sha="a" * 64)
    fresh = _readiness(height=101, sha="b" * 64)

    assert MODULE._readiness_static_binding(saved) == (
        MODULE._readiness_static_binding(fresh)
    )

    changed = copy.deepcopy(fresh)
    changed["decision_id"] = "different"
    assert MODULE._readiness_static_binding(saved) != (
        MODULE._readiness_static_binding(changed)
    )


def test_ready_admission_verifies_wallet_but_authorizes_nothing(monkeypatch):
    report = _build(monkeypatch)

    assert report["execution_admission_ready"] is True
    assert report["wallet_identity_matches_readiness"] is True
    assert report["executor_keypair_identity_verified"] is True
    assert report["wrapper_read_private_key_bytes"] is False
    assert report["fresh_current_block_height"] == 101
    assert report["fresh_block_height_remaining"] == 19
    assert report["requires_separate_single_shot_submitter"] is True
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_builder_rejects_fresh_static_readiness_drift(monkeypatch):
    fresh = _readiness(height=101, sha="b" * 64)
    fresh["decision_id"] = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

    with pytest.raises(ValueError, match="static binding differs"):
        _build(monkeypatch, fresh=fresh)


def test_builder_rejects_backwards_block_height(monkeypatch):
    with pytest.raises(ValueError, match="moved backwards"):
        _build(
            monkeypatch,
            saved=_readiness(height=100, sha="a" * 64),
            fresh=_readiness(height=99, sha="b" * 64),
        )


def test_builder_rejects_expired_blockhash(monkeypatch):
    fresh = _readiness(height=121, sha="b" * 64)
    fresh["block_height_remaining"] = 0

    with pytest.raises(ValueError, match="expired"):
        _build(monkeypatch, fresh=fresh)


def test_builder_rejects_loaded_wallet_mismatch(monkeypatch):
    with pytest.raises(ValueError, match="public key differs"):
        _build(
            monkeypatch,
            wallet_pubkey="33333333333333333333333333333333",
        )


def test_keypair_path_rejects_broad_permissions():
    if os.name != "posix":
        pytest.skip("POSIX permission semantics required")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "keypair.json"
        path.write_text("[1,2,3]", encoding="utf-8")
        path.chmod(0o644)

        with pytest.raises(ValueError, match="permissions are too broad"):
            MODULE._validate_keypair_path(str(path))


def test_keypair_path_rejects_symlink():
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "keypair.json"
        target.write_text("[1,2,3]", encoding="utf-8")
        if os.name == "posix":
            target.chmod(0o600)
        link = root / "keypair-link.json"
        link.symlink_to(target)

        with pytest.raises(ValueError, match="must not be a symlink"):
            MODULE._validate_keypair_path(str(link))


def test_resealed_admission_cannot_authorize_signing(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_signing_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_signing_authorized=false",
    ):
        MODULE.validate_execution_admission(report)


def test_resealed_admission_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_execution_admission(report)


def test_admission_tool_invokes_only_wallet_status_not_submitter():
    source = TOOL.read_text(encoding="utf-8")

    assert '"wallet-status"' in source
    assert '"controlled-live-submit"' not in source
    assert "send_transaction" not in source
    assert "sendTransaction" not in source
    assert "sign_prepared_transaction" not in source
    assert "sign_execution_intent" not in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
    assert '"wrapper_read_private_key_bytes": False' in source
