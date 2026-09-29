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
    / "build_phase7_controlled_live_exit_settlement_signed_authorization.py"
)
REQUEST_TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase7_controlled_live_exit_settlement_authorization_request.py"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load(
    TOOL,
    "phase7_exit_settlement_signed_authorization_test",
)
REQUEST = _load(
    REQUEST_TOOL,
    "phase7_exit_settlement_authorization_request_for_signed_test",
)


OPENED_DECISION_ID = "11111111-2222-4333-8444-555555555555"
EXIT_DECISION_ID = "01234567-89ab-4def-8123-456789abcdef"
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"
WALLET = "44444444444444444444444444444444"


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
        "finalization_sha256": "1" * 64,
        "preparation_sha256": "2" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "exit_signature": "sig-confirmed-exit",
        "zero_liquidity_snapshot_sha256": "3" * 64,
        "zero_liquidity_capture_slot_start": 130,
        "zero_liquidity_capture_slot_end": 130,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": "4" * 64,
        "destination_config_sha256": "5" * 64,
        "user_token_x": "55555555555555555555555555555555",
        "user_token_y": "66666666666666666666666666666666",
        "reward_token_destinations": [
            {
                "reward_index": 0,
                "user_token_account": "77777777777777777777777777777777",
            }
        ],
        "final_settlement_transaction_sha256": "6" * 64,
        "recent_blockhash": "11111111111111111111111111111111",
        "last_valid_block_height": 999,
        "prepared_rpc_context_slot": 140,
        "exact_simulation_sha256": "7" * 64,
        "exact_simulation_rpc_context_slot": 141,
        "final_guard_sha256": "8" * 64,
        "final_wallet_authorization_sha256": "9" * 64,
        "finalizer_binary_sha256": "a" * 64,
        "request_created_at": "2026-09-29T15:30:00Z",
        "settlement_finalization_verified": True,
        "transaction_unsigned": True,
        "exact_simulation_verified": True,
        "zero_liquidity_lineage_verified": True,
        "exact_settlement_transaction_authorization_request_ready": True,
        "explicit_human_exact_transaction_authorization_required": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "single_submission_only_required": True,
        "post_settlement_confirmation_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "exact_transaction_authorization_present": False,
        "settlement_authorized": False,
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
    request = {
        **identity,
        "request_sha256": hashlib.sha256(
            REQUEST._canonical_bytes(identity)
        ).hexdigest(),
    }
    REQUEST.validate_exit_settlement_authorization_request(request)
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
        approver_principal="approver@example.com",
        issued_at="2026-09-29T15:30:10Z",
        ttl_seconds=60,
        approval_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    )
    payload_path = _write(root, "payload.json", payload)
    return payload, payload_path, request_path


def _sign(
    root: Path,
    payload: dict,
    *,
    allowed_principal: str = "approver@example.com",
) -> tuple[Path, Path, str]:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen unavailable")

    key = root / "authorization_ed25519"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    public_key = (root / "authorization_ed25519.pub").read_text(
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


def _reseal_verification(report: dict) -> None:
    identity = {
        field: report[field] for field in MODULE.VERIFICATION_FIELDS
    }
    report["verification_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_settlement_authorization_request_tool_is_exactly_pinned():
    assert MODULE._git_blob_sha(ROOT / MODULE.REQUEST_TOOL) == (
        MODULE.REVIEWED_SOURCE_BLOBS[MODULE.REQUEST_TOOL]
    )


def test_signature_namespace_is_settlement_specific():
    assert MODULE.SIGNATURE_NAMESPACE == (
        "pio-phase7-controlled-live-exit-settlement-authorization-v1"
    )
    assert MODULE.MAX_TTL_SECONDS == 300


def test_payload_binds_exact_settlement_and_executes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    assert payload["request_sha256"] == request["request_sha256"]
    assert payload["finalization_sha256"] == request["finalization_sha256"]
    assert payload["destination_config_sha256"] == (
        request["destination_config_sha256"]
    )
    assert payload["final_settlement_transaction_sha256"] == (
        request["final_settlement_transaction_sha256"]
    )
    assert payload["recent_blockhash"] == request["recent_blockhash"]
    assert (
        payload["human_exact_settlement_transaction_authorization_intent"]
        is True
    )
    assert payload["exact_transaction_authorization_present"] is False
    assert payload["settlement_authorized"] is False
    assert payload["transaction_signing_authorized"] is False
    assert payload["transaction_submission_authorized"] is False


def test_authorization_cannot_predate_request():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = _write(root, "request.json", _request())

        with pytest.raises(ValueError, match="cannot predate"):
            MODULE.build_payload(
                source_tree=ROOT,
                request_path=request_path,
                approver_principal="approver@example.com",
                issued_at="2026-09-29T15:29:00Z",
                ttl_seconds=60,
            )


def test_authorization_ttl_is_short_lived():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = _write(root, "request.json", _request())

        with pytest.raises(ValueError, match="1..300"):
            MODULE.build_payload(
                source_tree=ROOT,
                request_path=request_path,
                approver_principal="approver@example.com",
                issued_at="2026-09-29T15:30:10Z",
                ttl_seconds=301,
            )


def test_request_with_existing_authorization_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request = _request()
        request["exact_transaction_authorization_present"] = True
        identity = {
            field: request[field] for field in REQUEST.REQUEST_FIELDS
        }
        request["request_sha256"] = hashlib.sha256(
            REQUEST._canonical_bytes(identity)
        ).hexdigest()
        request_path = _write(root, "request.json", request)

        with pytest.raises(ValueError):
            MODULE.build_payload(
                source_tree=ROOT,
                request_path=request_path,
                approver_principal="approver@example.com",
                issued_at="2026-09-29T15:30:10Z",
            )


def test_resealed_payload_cannot_pre_authorize_signing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    payload["transaction_signing_authorized"] = True
    _reseal_payload(payload)

    with pytest.raises(
        ValueError,
        match="transaction_signing_authorized=false",
    ):
        MODULE.validate_payload(payload, request=request)


def test_real_ed25519_settlement_authorization_round_trip():
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
            now="2026-09-29T15:30:30Z",
        )

    assert report["signature_verified"] is True
    assert report["trust_root_digest_matches"] is True
    assert (
        report["human_exact_settlement_transaction_authorization_verified"]
        is True
    )
    assert report["exact_transaction_authorization_present"] is True
    assert report["settlement_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_expired_settlement_authorization_fails_closed():
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
                now="2026-09-29T15:31:11Z",
            )


def test_wrong_trust_root_fails_closed():
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
                now="2026-09-29T15:30:30Z",
            )


def test_untrusted_authorizer_fails_closed():
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
                now="2026-09-29T15:30:30Z",
            )


def test_signature_does_not_survive_destination_tampering():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, request_path = _payload(root)
        signature, allowed, allowed_sha = _sign(root, payload)

        tampered = copy.deepcopy(payload)
        tampered["destination_config_sha256"] = "f" * 64
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
                now="2026-09-29T15:30:30Z",
            )


def test_signature_does_not_survive_transaction_tampering():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, request_path = _payload(root)
        signature, allowed, allowed_sha = _sign(root, payload)

        tampered = copy.deepcopy(payload)
        tampered["final_settlement_transaction_sha256"] = "f" * 64
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
                now="2026-09-29T15:30:30Z",
            )


def test_resealed_verification_cannot_authorize_submission():
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
            now="2026-09-29T15:30:30Z",
        )

    report["transaction_submission_authorized"] = True
    _reseal_verification(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_verification(report)


def test_signed_authorization_tool_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'shutil.which("ssh-keygen")' in source
    assert "str(_ssh_keygen_path())" in source
    assert "load_executor_keypair" not in source
    assert "controlled-live-submit" not in source
    assert "send_transaction" not in source
    assert "execution_store" not in source
    assert '"settlement_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
