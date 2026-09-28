from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase7_controlled_live_transaction_execution_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_transaction_execution_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _presubmit() -> dict:
    return {
        "presubmit_gate_sha256": "1" * 64,
        "decision_id": "11111111-2222-4333-8444-555555555555",
        "pool_address": "11111111111111111111111111111111",
        "executor_wallet_pubkey": "22222222222222222222222222222222",
        "prepared_transaction_sha256": "2" * 64,
        "final_simulation_sha256": "3" * 64,
        "execution_intent_snapshot_sha256": "4" * 64,
        "signature_absent": True,
        "error_absent": True,
        "final_simulation_succeeded": True,
    }


def _request() -> dict:
    return {
        "request_sha256": "5" * 64,
        "presubmit_gate_sha256": "1" * 64,
        "decision_id": "11111111-2222-4333-8444-555555555555",
        "pool_address": "11111111111111111111111111111111",
        "executor_wallet_pubkey": "22222222222222222222222222222222",
        "prepared_transaction_sha256": "2" * 64,
        "final_simulation_sha256": "3" * 64,
        "execution_intent_snapshot_sha256": "4" * 64,
        "prepared_last_valid_block_height": 1000,
    }


def _verification() -> dict:
    return {
        "verification_sha256": "6" * 64,
        "request_sha256": "5" * 64,
        "human_transaction_execution_authorization_verified": True,
    }


def _report() -> dict:
    identity = {
        "format_version": MODULE.FORMAT_VERSION,
        "artifact_type": MODULE.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                MODULE.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "saved_presubmit_gate_sha256": "1" * 64,
        "fresh_presubmit_gate_sha256": "1" * 64,
        "saved_transaction_request_sha256": "5" * 64,
        "fresh_transaction_request_sha256": "5" * 64,
        "saved_transaction_authorization_verification_sha256": "6" * 64,
        "fresh_transaction_authorization_verification_sha256": "6" * 64,
        "decision_id": "11111111-2222-4333-8444-555555555555",
        "pool_address": "11111111111111111111111111111111",
        "executor_wallet_pubkey": "22222222222222222222222222222222",
        "prepared_transaction_sha256": "2" * 64,
        "final_simulation_sha256": "3" * 64,
        "execution_intent_snapshot_sha256": "4" * 64,
        "prepared_last_valid_block_height": 1000,
        "current_block_height": 900,
        "block_height_remaining": 100,
        "blockhash_not_expired": True,
        "rpc_commitment": MODULE.RPC_COMMITMENT,
        "rpc_endpoint_sha256": "7" * 64,
        "executor_binary_path": "/opt/pio/bin/meteora-executor",
        "executor_binary_sha256": "8" * 64,
        "expected_executor_binary_sha256": "8" * 64,
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
        "transaction_execution_readiness_ready": True,
        "readiness_only": True,
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
    return {
        **identity,
        "readiness_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    fresh_presubmit: dict | None = None,
    fresh_request: dict | None = None,
    fresh_verification: dict | None = None,
    block_height: int = 900,
    runtime_opt_in: str | None = "1",
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    production.mkdir()
    source = ROOT

    saved_presubmit = _presubmit()
    saved_request = _request()
    saved_verification = _verification()

    class FakePresubmit:
        @staticmethod
        def validate_phase7_presubmit_evidence_gate(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase7_presubmit_evidence_gate(**kwargs):
            return copy.deepcopy(
                fresh_presubmit
                if fresh_presubmit is not None
                else saved_presubmit
            )

    class FakeRequest:
        @staticmethod
        def validate_phase7_transaction_execution_request(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase7_transaction_execution_request(**kwargs):
            return copy.deepcopy(
                fresh_request if fresh_request is not None else saved_request
            )

    class FakeSigner:
        @staticmethod
        def validate_verification(value):
            assert isinstance(value, dict)

        @staticmethod
        def verify_authorization(**kwargs):
            return copy.deepcopy(
                fresh_verification
                if fresh_verification is not None
                else saved_verification
            )

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed_modules",
        lambda source: (FakePresubmit, FakeRequest, FakeSigner),
    )
    monkeypatch.setattr(
        MODULE,
        "_verify_executor_binary",
        lambda **kwargs: (Path("/opt/pio/bin/meteora-executor"), "8" * 64),
    )
    monkeypatch.setattr(
        MODULE,
        "_rpc_block_height",
        lambda rpc_url: block_height,
    )

    if runtime_opt_in is None:
        monkeypatch.delenv(MODULE.LIVE_SUBMIT_ENV_NAME, raising=False)
    else:
        monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV_NAME, runtime_opt_in)

    args = dict(
        repository=production,
        source_tree=source,
        phase6_post_promotion_audit_path=root / "phase6.json",
        saved_phase7_evidence_status_path=root / "phase7-status.json",
        saved_phase7_evidence_plan_path=root / "phase7-plan.json",
        saved_input_preflight_path=root / "preflight.json",
        controlled_live_config_path=root / "controlled.json",
        proposal_path=root / "proposal.json",
        executor_wallet_pubkey="22222222222222222222222222222222",
        saved_controlled_live_authorization_verification_path=(
            root / "controlled-verification.json"
        ),
        controlled_live_signed_payload_path=root / "controlled-payload.json",
        controlled_live_signature_path=root / "controlled.sig",
        controlled_live_allowed_signers_path=root / "controlled-allowed",
        expected_controlled_live_allowed_signers_sha256="a" * 64,
        saved_authorization_readiness_path=root / "authorization-readiness.json",
        expected_authorization_readiness_sha256="b" * 64,
        execution_database_path=root / "execution.db",
        saved_presubmit_gate_path=_write(
            root, "saved-presubmit.json", saved_presubmit
        ),
        transaction_request_path=_write(
            root, "transaction-request.json", saved_request
        ),
        saved_transaction_authorization_verification_path=_write(
            root, "transaction-verification.json", saved_verification
        ),
        transaction_signed_payload_path=root / "transaction-payload.json",
        transaction_signature_path=root / "transaction.sig",
        transaction_allowed_signers_path=root / "transaction-allowed",
        expected_transaction_allowed_signers_sha256="c" * 64,
        executor_binary_path=root / "meteora-executor",
        expected_executor_binary_sha256="8" * 64,
        rpc_url="https://rpc.example.invalid",
        now="2026-09-28T20:00:00Z",
    )

    def execute():
        return MODULE.build_transaction_execution_readiness(**args)

    return temp, execute


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_report_is_still_readiness_only():
    report = _report()

    MODULE.validate_transaction_execution_readiness(copy.deepcopy(report))

    assert report["transaction_execution_readiness_ready"] is True
    assert report["readiness_only"] is True
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_resealed_report_cannot_authorize_signing():
    report = _report()
    report["transaction_signing_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="transaction_signing_authorized=false"):
        MODULE.validate_transaction_execution_readiness(report)


def test_resealed_report_cannot_authorize_submission():
    report = _report()
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_transaction_execution_readiness(report)


def test_resealed_report_cannot_authorize_live_capital():
    report = _report()
    report["live_capital_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="live_capital_authorized=false"):
        MODULE.validate_transaction_execution_readiness(report)


def test_validator_rejects_expired_blockhash():
    report = _report()
    report["current_block_height"] = 1001
    report["block_height_remaining"] = 0
    _reseal(report)

    with pytest.raises(ValueError, match="blockhash is expired"):
        MODULE.validate_transaction_execution_readiness(report)


def test_executor_binary_requires_expected_hash_and_live_submit_guard():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        binary = root / "meteora-executor"
        payload = b"prefix-" + MODULE.LIVE_SUBMIT_RUNTIME_GUARD + b"-suffix"
        binary.write_bytes(payload)
        expected = hashlib.sha256(payload).hexdigest()

        path, digest = MODULE._verify_executor_binary(
            executor_binary_path=binary,
            expected_sha256=expected,
        )

        assert path == binary.resolve()
        assert digest == expected


def test_executor_binary_wrong_hash_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / "meteora-executor"
        binary.write_bytes(MODULE.LIVE_SUBMIT_RUNTIME_GUARD)

        with pytest.raises(ValueError, match="does not match external trust root"):
            MODULE._verify_executor_binary(
                executor_binary_path=binary,
                expected_sha256="f" * 64,
            )


def test_executor_binary_missing_live_submit_guard_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / "meteora-executor"
        payload = b"binary-without-live-submit-feature"
        binary.write_bytes(payload)

        with pytest.raises(ValueError, match="does not expose"):
            MODULE._verify_executor_binary(
                executor_binary_path=binary,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )


def test_rpc_block_height_uses_read_only_get_block_height(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": 987}
            ).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(MODULE.urllib_request, "urlopen", fake_urlopen)

    height = MODULE._rpc_block_height("https://rpc.example.invalid")

    assert height == 987
    assert captured["method"] == "POST"
    assert captured["body"]["method"] == "getBlockHeight"
    assert captured["body"]["params"] == [{"commitment": "confirmed"}]


def test_builder_reproduces_all_saved_artifacts_and_stays_non_authorizing(
    monkeypatch,
):
    temp, execute = _build(monkeypatch)
    try:
        report = execute()
    finally:
        temp.cleanup()

    MODULE.validate_transaction_execution_readiness(report)
    assert report["fresh_presubmit_matches_saved"] is True
    assert report["fresh_request_matches_saved"] is True
    assert report["fresh_transaction_authorization_matches_saved"] is True
    assert report["block_height_remaining"] == 100
    assert report["runtime_live_submit_opt_in_present"] is True
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_builder_rejects_presubmit_drift(monkeypatch):
    fresh = _presubmit()
    fresh["signature_absent"] = False

    temp, execute = _build(monkeypatch, fresh_presubmit=fresh)
    try:
        with pytest.raises(ValueError, match="differs from saved gate"):
            execute()
    finally:
        temp.cleanup()


def test_builder_rejects_request_drift(monkeypatch):
    fresh = _request()
    fresh["prepared_last_valid_block_height"] = 999

    temp, execute = _build(monkeypatch, fresh_request=fresh)
    try:
        with pytest.raises(ValueError, match="differs from saved request"):
            execute()
    finally:
        temp.cleanup()


def test_builder_rejects_transaction_authorization_drift(monkeypatch):
    fresh = _verification()
    fresh["verification_sha256"] = "d" * 64

    temp, execute = _build(monkeypatch, fresh_verification=fresh)
    try:
        with pytest.raises(ValueError, match="differs from saved verification"):
            execute()
    finally:
        temp.cleanup()


def test_builder_rejects_expired_block_height(monkeypatch):
    temp, execute = _build(monkeypatch, block_height=1001)
    try:
        with pytest.raises(ValueError, match="blockhash has expired"):
            execute()
    finally:
        temp.cleanup()


def test_builder_requires_runtime_live_submit_opt_in(monkeypatch):
    temp, execute = _build(monkeypatch, runtime_opt_in=None)
    try:
        with pytest.raises(ValueError, match="runtime live-submit opt-in is absent"):
            execute()
    finally:
        temp.cleanup()


def test_readiness_tool_has_no_transaction_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "send_transaction" not in source
    assert "load_executor_keypair" not in source
    assert "sign_execution_intent" not in source
    assert "sign_prepared_transaction" not in source
    assert "systemctl" not in source
    assert "subprocess" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"readiness_only": True' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
