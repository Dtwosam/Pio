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
    / "build_manual_market_paper_preserved_signed_human_authorization.py"
)
REQUEST_TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_preserved_human_authorization_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_preserved_signed_human_authorization",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

REQUEST_SPEC = importlib.util.spec_from_file_location(
    "preserved_signed_human_auth_request_module",
    REQUEST_TOOL,
)
assert REQUEST_SPEC is not None and REQUEST_SPEC.loader is not None
REQUEST = importlib.util.module_from_spec(REQUEST_SPEC)
sys.modules[REQUEST_SPEC.name] = REQUEST
REQUEST_SPEC.loader.exec_module(REQUEST)


def _operation(
    *,
    index: int = 0,
    operation: str = "UPDATE_PRESERVED_FILE",
    path: str = "rust-executor/src/state_reader.rs",
    backup_required: bool = True,
) -> dict:
    expected = "1" * 40 if backup_required else None
    return {
        "index": index,
        "layer": "STATE_READER",
        "operation": operation,
        "path": path,
        "plan_operation_sha256": "2" * 64,
        "expected_current_blob": expected,
        "target_blob": "3" * 40,
        "rollback_operation": (
            "RESTORE_EXPECTED_BLOB"
            if backup_required
            else "DELETE_CREATED_FILE"
        ),
        "rollback_blob": expected if backup_required else None,
        "backup_required": backup_required,
        "backup_relative_path": (
            f"files/{path}" if backup_required else None
        ),
        "backup_git_blob": expected if backup_required else None,
        "backup_sha256": "4" * 64 if backup_required else None,
        "backup_size": 123 if backup_required else None,
        "backup_mode": 0o644 if backup_required else None,
    }


def _request() -> dict:
    operations = [
        _operation(),
        _operation(
            index=1,
            operation="CREATE_FILE",
            path="python-learner/src/meteora_learner/runtime_overlay.py",
            backup_required=False,
        ),
    ]
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
        "authorization_review_sha256": "5" * 64,
        "production_repository": "/opt/pio",
        "backup_dir": "/var/tmp/pio-preserved-backups.test",
        "authorization_scope": REQUEST.AUTHORIZATION_SCOPE,
        "excluded_scopes": list(REQUEST.EXCLUDED_SCOPES),
        "operations": operations,
        "operation_count": len(operations),
        "authorization_request_ready": True,
        "approval_artifact_present": False,
        "authorization_granted": False,
        "explicit_human_authorization_required": True,
        "fresh_execution_recheck_required": True,
        "requires_separate_mutation_authorization": True,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    request = {
        **identity,
        "request_sha256": hashlib.sha256(
            REQUEST._canonical_bytes(identity)
        ).hexdigest(),
    }
    REQUEST.validate_authorization_request(request)
    return request


def _write_request(root: Path, request: dict) -> Path:
    path = root / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    return path


def _build_payload(root: Path, request: dict) -> tuple[dict, Path]:
    request_path = _write_request(root, request)
    payload = MODULE.build_authorization_payload(
        source_tree=ROOT,
        request_path=request_path,
        approver_principal="wyck@example.com",
        issued_at="2026-09-28T10:00:00Z",
        ttl_seconds=300,
        approval_id="01234567-89ab-4def-8123-456789abcdef",
    )
    payload_path = root / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload, payload_path


def _sign_payload(
    root: Path,
    payload: dict,
    *,
    principal: str = "wyck@example.com",
) -> tuple[Path, Path, str]:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is unavailable")

    key_path = root / "approval_ed25519"
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(key_path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    public_key = (root / "approval_ed25519.pub").read_text(
        encoding="utf-8"
    ).strip()
    allowed_signers = root / "allowed_signers"
    allowed_signers.write_text(
        f"{principal} {public_key}\n",
        encoding="utf-8",
    )

    signing_bytes = root / "payload.canonical"
    signing_bytes.write_bytes(MODULE._canonical_bytes(payload))
    subprocess.run(
        [
            "ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(key_path),
            "-n",
            MODULE.SIGNATURE_NAMESPACE,
            str(signing_bytes),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    signature = Path(str(signing_bytes) + ".sig")
    assert signature.is_file()

    allowed_sha = hashlib.sha256(allowed_signers.read_bytes()).hexdigest()
    return signature, allowed_signers, allowed_sha


def _reseal_payload(payload: dict) -> None:
    identity = {field: payload[field] for field in MODULE.PAYLOAD_FIELDS}
    payload["payload_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_human_authorization_request_tool_is_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_payload_binds_exact_request_and_remains_non_executable():
    with tempfile.TemporaryDirectory() as tmp:
        payload, _ = _build_payload(Path(tmp), _request())

    assert payload["decision"] == MODULE.DECISION
    assert payload["authorization_scope"] == MODULE.AUTHORIZATION_SCOPE
    assert payload["human_authorization_intent"] is True
    assert payload["fresh_execution_recheck_required"] is True
    assert payload["execution_ready"] is False
    assert payload["mutation_authorized"] is False
    assert payload["request_sha256"] == _request()["request_sha256"]


def test_signing_bytes_are_exact_validated_canonical_payload():
    request = _request()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path = _build_payload(root, request)
        emitted = MODULE.authorization_signing_bytes(
            source_tree=ROOT,
            request_path=root / "request.json",
            payload_path=payload_path,
        )

    assert emitted == MODULE._canonical_bytes(payload)
    assert not emitted.endswith(b"\\n")


def test_resealed_payload_cannot_become_execution_ready():
    request = _request()
    with tempfile.TemporaryDirectory() as tmp:
        payload, _ = _build_payload(Path(tmp), request)

    payload["execution_ready"] = True
    _reseal_payload(payload)

    with pytest.raises(ValueError, match="not execution-ready"):
        MODULE.validate_authorization_payload(payload, request=request)


def test_resealed_payload_cannot_authorize_mutation():
    request = _request()
    with tempfile.TemporaryDirectory() as tmp:
        payload, _ = _build_payload(Path(tmp), request)

    payload["mutation_authorized"] = True
    _reseal_payload(payload)

    with pytest.raises(ValueError, match="mutation_authorized=false"):
        MODULE.validate_authorization_payload(payload, request=request)


def test_payload_cannot_outlive_maximum_window():
    request = _request()
    with tempfile.TemporaryDirectory() as tmp:
        request_path = _write_request(Path(tmp), request)
        with pytest.raises(ValueError, match="ttl_seconds"):
            MODULE.build_authorization_payload(
                source_tree=ROOT,
                request_path=request_path,
                approver_principal="wyck@example.com",
                issued_at="2026-09-28T10:00:00Z",
                ttl_seconds=MODULE.MAX_TTL_SECONDS + 1,
            )


def test_detached_ed25519_signature_round_trip_is_verified():
    request = _request()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path = _build_payload(root, request)
        signature, allowed_signers, allowed_sha = _sign_payload(
            root,
            payload,
        )
        request_path = root / "request.json"

        report = MODULE.verify_authorization(
            source_tree=ROOT,
            request_path=request_path,
            payload_path=payload_path,
            signature_path=signature,
            allowed_signers_path=allowed_signers,
            expected_allowed_signers_sha256=allowed_sha,
            now="2026-09-28T10:02:00Z",
        )

    assert report["signature_verified"] is True
    assert report["trust_root_digest_matches"] is True
    assert report["human_authorization_verified"] is True
    assert report["fresh_execution_recheck_required"] is True
    assert report["execution_ready"] is False
    assert report["mutation_authorized"] is False
    assert report["approver_principal"] == "wyck@example.com"


def test_valid_signature_with_wrong_trust_root_digest_fails_closed():
    request = _request()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path = _build_payload(root, request)
        signature, allowed_signers, _ = _sign_payload(root, payload)

        with pytest.raises(ValueError, match="trust-root digest mismatch"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=root / "request.json",
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed_signers,
                expected_allowed_signers_sha256="f" * 64,
                now="2026-09-28T10:02:00Z",
            )


def test_signature_from_untrusted_principal_fails_closed():
    request = _request()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path = _build_payload(root, request)
        signature, allowed_signers, allowed_sha = _sign_payload(
            root,
            payload,
            principal="somebody-else@example.com",
        )

        with pytest.raises(ValueError, match="signature verification failed"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=root / "request.json",
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed_signers,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-28T10:02:00Z",
            )


def test_expired_signed_authorization_fails_before_execution_boundary():
    request = _request()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path = _build_payload(root, request)
        signature, allowed_signers, allowed_sha = _sign_payload(root, payload)

        with pytest.raises(ValueError, match="has expired"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=root / "request.json",
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed_signers,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-28T10:06:00Z",
            )


def test_signature_does_not_survive_resealed_payload_tampering():
    request = _request()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path = _build_payload(root, request)
        signature, allowed_signers, allowed_sha = _sign_payload(root, payload)

        tampered = copy.deepcopy(payload)
        tampered["approval_id"] = "fedcba98-7654-4cba-8123-456789abcdef"
        _reseal_payload(tampered)
        payload_path.write_text(json.dumps(tampered), encoding="utf-8")

        with pytest.raises(ValueError, match="signature verification failed"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=root / "request.json",
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed_signers,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-28T10:02:00Z",
            )


def test_signed_authorization_tool_has_no_production_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "/opt/pio" not in source
    assert "systemctl" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "shell=True" not in source
    assert '"execution_ready": False' in source
    assert '"mutation_authorized": False' in source
    assert '"human_authorization_verified": True' in source
