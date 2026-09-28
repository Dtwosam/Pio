from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase7_controlled_live_transaction_signed_authorization.py"
)
REQUEST_TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase7_controlled_live_transaction_request.py"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load(TOOL, "phase7_transaction_signed_authorization_test")
REQUEST = _load(REQUEST_TOOL, "phase7_transaction_request_for_signed_test")


POOL = "11111111111111111111111111111111"
WALLET = "22222222222222222222222222222222"
DECISION_ID = "11111111-2222-4333-8444-555555555555"


def _request() -> dict:
    identity = {
        "format_version": REQUEST.FORMAT_VERSION,
        "artifact_type": REQUEST.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REQUEST.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "authorization_scope": REQUEST.AUTHORIZATION_SCOPE,
        "excluded_scopes": list(REQUEST.EXCLUDED_SCOPES),
        "presubmit_gate_sha256": "1" * 64,
        "authorization_readiness_sha256": "2" * 64,
        "decision_id": DECISION_ID,
        "pool_address": POOL,
        "executor_wallet_pubkey": WALLET,
        "phase7_evidence_status_sha256": "3" * 64,
        "phase7_evidence_plan_sha256": "4" * 64,
        "input_preflight_sha256": "5" * 64,
        "controlled_live_authorization_verification_sha256": "6" * 64,
        "controlled_live_config_sha256": "7" * 64,
        "proposal_sha256": "8" * 64,
        "input_manifest_sha256": "9" * 64,
        "execution_request_sha256": "a" * 64,
        "risk_report_sha256": "b" * 64,
        "transaction_guard_sha256": "c" * 64,
        "wallet_authorization_sha256": "d" * 64,
        "prepared_transaction_sha256": "e" * 64,
        "final_simulation_sha256": "f" * 64,
        "execution_intent_snapshot_sha256": "0" * 64,
        "prepared_last_valid_block_height": 123,
        "prepared_rpc_context_slot": 100,
        "final_simulation_rpc_context_slot": 101,
        "presubmit_evidence_ready": True,
        "transaction_execution_request_ready": True,
        "explicit_human_transaction_authorization_required": True,
        "fresh_presubmit_recheck_required": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "live_submit_feature_required": True,
        "runtime_live_submit_opt_in_required": True,
        "confirmation_receipt_reconciliation_required": True,
        "separate_phase7_promotion_required": True,
        "transaction_execution_authorization_present": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "phase7_promotion_persisted": False,
    }
    request = {
        **identity,
        "request_sha256": REQUEST._sha256(identity),
    }
    REQUEST.validate_phase7_transaction_execution_request(request)
    return request


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _payload(root: Path) -> tuple[dict, Path, Path]:
    request = _request()
    request_path = _write(root, "request.json", request)
    payload = MODULE.build_payload(
        source_tree=ROOT,
        request_path=request_path,
        approver_principal="wyck@example.com",
        issued_at="2026-09-28T20:00:00Z",
        ttl_seconds=120,
        approval_id="01234567-89ab-4def-8123-456789abcdef",
    )
    payload_path = _write(root, "payload.json", payload)
    return payload, payload_path, request_path


def _sign(
    root: Path,
    payload: dict,
    *,
    allowed_principal: str = "wyck@example.com",
) -> tuple[Path, Path, str]:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen unavailable")

    key = root / "approval_ed25519"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    public_key = (root / "approval_ed25519.pub").read_text(
        encoding="utf-8"
    ).strip()
    allowed = root / "allowed_signers"
    allowed.write_text(
        f"{allowed_principal} {public_key}\n",
        encoding="utf-8",
    )

    signing = root / "payload.canonical"
    signing.write_bytes(MODULE._canonical_bytes(payload))
    subprocess.run(
        [
            "ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(key),
            "-n",
            MODULE.SIGNATURE_NAMESPACE,
            str(signing),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    signature = Path(str(signing) + ".sig")
    return (
        signature,
        allowed,
        hashlib.sha256(allowed.read_bytes()).hexdigest(),
    )


def _reseal_payload(payload: dict) -> None:
    identity = {field: payload[field] for field in MODULE.PAYLOAD_FIELDS}
    payload["payload_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_request_tool_is_exactly_pinned():
    assert MODULE._git_blob_sha(ROOT / MODULE.REQUEST_TOOL) == (
        MODULE.REVIEWED_SOURCE_BLOBS[MODULE.REQUEST_TOOL]
    )


def test_transaction_signature_namespace_is_dedicated():
    assert MODULE.SIGNATURE_NAMESPACE == (
        "pio-phase7-controlled-live-transaction-authorization-v1"
    )
    assert MODULE.MAX_TTL_SECONDS == 300


def test_payload_binds_exact_transaction_but_still_authorizes_no_execution():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    assert payload["request_sha256"] == request["request_sha256"]
    assert (
        payload["prepared_transaction_sha256"]
        == request["prepared_transaction_sha256"]
    )
    assert payload["decision_id"] == DECISION_ID
    assert payload["human_transaction_authorization_intent"] is True
    assert payload["fresh_presubmit_recheck_required"] is True
    assert payload["fresh_blockhash_expiry_recheck_required"] is True
    assert payload["final_transaction_execution_gate_required"] is True
    assert payload["transaction_signing_authorized"] is False
    assert payload["transaction_submission_authorized"] is False
    assert payload["live_capital_authorized"] is False


def test_payload_ttl_above_five_minutes_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = _write(root, "request.json", _request())

        with pytest.raises(ValueError, match="ttl_seconds"):
            MODULE.build_payload(
                source_tree=ROOT,
                request_path=request_path,
                approver_principal="wyck@example.com",
                issued_at="2026-09-28T20:00:00Z",
                ttl_seconds=301,
            )


def test_resealed_payload_cannot_authorize_signing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    payload["transaction_signing_authorized"] = True
    _reseal_payload(payload)

    with pytest.raises(ValueError, match="transaction_signing_authorized=false"):
        MODULE.validate_payload(payload, request=request)


def test_real_ed25519_transaction_authorization_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, request_path = _payload(root)
        signature, allowed, allowed_sha = _sign(root, payload)

        report = MODULE.verify_authorization(
            source_tree=ROOT,
            request_path=request_path,
            payload_path=payload_path,
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256=allowed_sha,
            now="2026-09-28T20:01:00Z",
        )

    assert report["signature_verified"] is True
    assert report["trust_root_digest_matches"] is True
    assert report["human_transaction_execution_authorization_verified"] is True
    assert report["final_transaction_execution_gate_required"] is True
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_expired_transaction_authorization_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, request_path = _payload(root)
        signature, allowed, allowed_sha = _sign(root, payload)

        with pytest.raises(ValueError, match="has expired"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=request_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-28T20:02:01Z",
            )


def test_wrong_transaction_trust_root_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, request_path = _payload(root)
        signature, allowed, _ = _sign(root, payload)

        with pytest.raises(ValueError, match="trust-root digest mismatch"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=request_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256="f" * 64,
                now="2026-09-28T20:01:00Z",
            )


def test_untrusted_transaction_principal_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, request_path = _payload(root)
        signature, allowed, allowed_sha = _sign(
            root,
            payload,
            allowed_principal="somebody-else@example.com",
        )

        with pytest.raises(ValueError, match="signature verification failed"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=request_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-28T20:01:00Z",
            )


def test_signature_does_not_survive_prepared_transaction_tampering():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, request_path = _payload(root)
        signature, allowed, allowed_sha = _sign(root, payload)

        tampered = copy.deepcopy(payload)
        tampered["prepared_transaction_sha256"] = "1" * 64
        _reseal_payload(tampered)
        payload_path.write_text(json.dumps(tampered), encoding="utf-8")

        with pytest.raises(ValueError):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=request_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-28T20:01:00Z",
            )


def test_transaction_authorization_tool_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "load_executor_keypair" not in source
    assert "submit_execution_intent" not in source
    assert "begin_signing(" not in source
    assert "record_sent(" not in source
    assert "systemctl" not in source
    assert "PIO_LIVE_SUBMIT_ENABLED" not in source
    assert "persist_phase7_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
