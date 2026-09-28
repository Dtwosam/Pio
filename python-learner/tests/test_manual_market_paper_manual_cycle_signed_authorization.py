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
    / "build_manual_market_paper_manual_cycle_signed_authorization.py"
)
REQUEST_TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_manual_cycle_authorization_request.py"
)
READINESS_TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_manual_market_paper_manual_cycle_readiness.py"
)

def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load(TOOL, "manual_cycle_signed_authorization_test")
REQUEST = _load(REQUEST_TOOL, "manual_cycle_auth_request_for_signed_test")
READINESS = _load(READINESS_TOOL, "manual_cycle_readiness_for_signed_test")


def _readiness() -> dict:
    identity = {
        "format_version": READINESS.FORMAT_VERSION,
        "artifact_type": READINESS.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                READINESS.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "saved_post_mutation_audit_sha256": "1" * 64,
        "fresh_post_mutation_audit_sha256": "1" * 64,
        "production_repository": "/opt/pio",
        "mutation_receipt_sha256": "2" * 64,
        "execution_precheck_sha256": "3" * 64,
        "approver_principal": "wyck@example.com",
        "approval_id": "01234567-89ab-4def-8123-456789abcdef",
        "runtime_manifest_git_blob": READINESS.REVIEWED_SOURCE_BLOBS[
            READINESS.RUNTIME_MANIFEST
        ],
        "manual_cycle_cli_git_blob": READINESS.REVIEWED_SOURCE_BLOBS[
            READINESS.MANUAL_CYCLE_CLI
        ],
        "fresh_post_mutation_audit_matches_saved": True,
        "post_mutation_audit_ready": True,
        "runtime_files_deployed": True,
        "manual_paper_runtime_ready": True,
        "manual_mode_safe": True,
        "operational_services_healthy": True,
        "activation_state_unchanged": True,
        "activation_observed": False,
        "runtime_manifest_manual_only": True,
        "manual_cycle_cli_reviewed": True,
        "manual_cycle_readiness_ready": True,
        "requires_separate_manual_cycle_authorization": True,
        "manual_cycle_execution_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
    }
    report = {
        **identity,
        "manual_cycle_readiness_sha256": hashlib.sha256(
            READINESS._canonical_bytes(identity)
        ).hexdigest(),
    }
    READINESS.validate_manual_cycle_readiness(report)
    return report


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _request(root: Path) -> tuple[dict, Path]:
    readiness_path = _write(root, "readiness.json", _readiness())
    request = REQUEST.build_manual_cycle_authorization_request(
        source_tree=ROOT,
        readiness_path=readiness_path,
        account="pio-proof-1",
        run_id="manual-proof-20260928-1",
        capital_per_position_quote="100.00",
        network_cost_quote="0.10",
    )
    return request, _write(root, "request.json", request)


def _payload(root: Path) -> tuple[dict, Path, Path]:
    request, request_path = _request(root)
    payload = MODULE.build_payload(
        source_tree=ROOT,
        request_path=request_path,
        approver_principal="wyck@example.com",
        issued_at="2026-09-28T12:00:00Z",
        ttl_seconds=300,
        approval_id="fedcba98-7654-4cba-8123-456789abcdef",
    )
    return payload, _write(root, "payload.json", payload), request_path


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


def test_payload_binds_exact_request_and_remains_non_executable():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    assert payload["request_sha256"] == request["request_sha256"]
    assert (
        payload["cycle_parameters_sha256"]
        == request["cycle_parameters_sha256"]
    )
    assert payload["account"] == "pio-proof-1"
    assert payload["run_id"] == "manual-proof-20260928-1"
    assert payload["one_cycle_only"] is True
    assert payload["paper_only"] is True
    assert payload["human_authorization_intent"] is True
    assert payload["manual_cycle_execution_authorized"] is False
    assert payload["paper_timer_enable_authorized"] is False
    assert payload["live_capital_authorized"] is False


def test_emit_signing_bytes_exactly_matches_verifier_canonicalization():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, request_path = _payload(root)
        emitted = MODULE.emit_signing_bytes(
            source_tree=ROOT,
            request_path=request_path,
            payload_path=payload_path,
        )
    assert emitted == MODULE._canonical_bytes(payload)
    assert not emitted.endswith(b"\n")


def test_resealed_payload_cannot_authorize_cycle_execution():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    payload["manual_cycle_execution_authorized"] = True
    _reseal_payload(payload)

    with pytest.raises(ValueError, match="manual_cycle_execution_authorized=false"):
        MODULE.validate_payload(payload, request=request)


def test_resealed_payload_cannot_enable_timer():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    payload["paper_timer_enable_authorized"] = True
    _reseal_payload(payload)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_payload(payload, request=request)


def test_real_ed25519_signature_round_trip_verifies():
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
            now="2026-09-28T12:02:00Z",
        )

    assert report["signature_verified"] is True
    assert report["trust_root_digest_matches"] is True
    assert report["human_cycle_authorization_verified"] is True
    assert report["fresh_manual_cycle_readiness_recheck_required"] is True
    assert report["manual_cycle_execution_authorized"] is False
    assert report["paper_timer_enable_authorized"] is False


def test_wrong_trust_root_digest_fails_closed():
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
                now="2026-09-28T12:02:00Z",
            )


def test_untrusted_principal_fails_closed():
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
                now="2026-09-28T12:02:00Z",
            )


def test_expired_authorization_fails_closed():
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
                now="2026-09-28T12:06:00Z",
            )


def test_signature_does_not_survive_resealed_payload_tampering():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, request_path = _payload(root)
        signature, allowed, allowed_sha = _sign(root, payload)

        tampered = copy.deepcopy(payload)
        tampered["approval_id"] = "11111111-2222-4333-8444-555555555555"
        _reseal_payload(tampered)
        payload_path.write_text(json.dumps(tampered), encoding="utf-8")

        with pytest.raises(ValueError, match="signature verification failed"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=request_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-28T12:02:00Z",
            )


def test_signed_authorization_tool_has_no_cycle_or_activation_executor():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "systemctl" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert "manual_market_paper_cycle_cli" not in source
    assert '"manual_cycle_execution_authorized": False' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
