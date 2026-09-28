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
    / "build_manual_market_paper_phase5_evidence_collection_signed_authorization.py"
)
REQUEST_TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_phase5_evidence_collection_request.py"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load(TOOL, "phase5_collection_signed_authorization_test")
REQUEST = _load(
    REQUEST_TOOL,
    "phase5_collection_request_for_signed_authorization_test",
)


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
        "collection_plan_sha256": "1" * 64,
        "account": "pio-proof-1",
        "run_id": "phase5-collection-20260928-1",
        "authorization_scope": REQUEST.AUTHORIZATION_SCOPE,
        "excluded_scopes": list(REQUEST.EXCLUDED_SCOPES),
        "scheduler_service_blob": "2" * 40,
        "scheduler_timer_blob": "3" * 40,
        "scheduler_cli_blob": "4" * 40,
        "scheduler_module_blob": "5" * 40,
        "scheduler_interval_seconds": REQUEST.SCHEDULER_INTERVAL_SECONDS,
        "scheduler_lease_seconds": REQUEST.SCHEDULER_LEASE_SECONDS,
        "scheduler_extra_args": list(REQUEST.SAFE_SCHEDULER_EXTRA_ARGS),
        "scheduler_extra_args_text": REQUEST.SAFE_SCHEDULER_EXTRA_ARGS_TEXT,
        "max_positions": 3,
        "refresh_jupiter_quotes": True,
        "requested_collection_seconds": 60 * 3600,
        "requested_collection_hours": 60.0,
        "nominal_timer_slots": 720,
        "entry_diversity_evidence_needed": True,
        "requires_separate_manual_entry_authorization": True,
        "automatic_stop_required": True,
        "fresh_collection_readiness_recheck_required": True,
        "explicit_human_authorization_required": True,
        "collection_request_ready": True,
        "collection_authorization_present": False,
        "collection_execution_authorized": False,
        "new_market_entry_authorized": False,
        "phase5_promotion_persisted": False,
        "phase5_promotion_authorized": False,
        "recurring_paper_automation_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_paper_database_modified": False,
    }
    request = {
        **identity,
        "request_sha256": hashlib.sha256(
            REQUEST._canonical_bytes(identity)
        ).hexdigest(),
    }
    REQUEST.validate_phase5_evidence_collection_request(request)
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
        issued_at="2026-09-28T14:00:00Z",
        ttl_seconds=300,
        approval_id="01234567-89ab-4def-8123-456789abcdef",
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


def test_payload_binds_exact_collection_and_remains_non_executable():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    assert payload["request_sha256"] == request["request_sha256"]
    assert payload["collection_plan_sha256"] == request["collection_plan_sha256"]
    assert payload["account"] == "pio-proof-1"
    assert payload["requested_collection_seconds"] == 60 * 3600
    assert payload["scheduler_extra_args_text"] == (
        "--max-positions 3 --refresh-jupiter-quotes"
    )
    assert payload["automatic_stop_required"] is True
    assert payload["human_authorization_intent"] is True
    assert payload["collection_execution_authorized"] is False
    assert payload["paper_timer_enable_authorized"] is False
    assert payload["new_market_entry_authorized"] is False
    assert payload["live_capital_authorized"] is False


def test_emit_signing_bytes_matches_verifier_canonicalization():
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


def test_resealed_payload_cannot_enable_timer():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    payload["paper_timer_enable_authorized"] = True
    _reseal_payload(payload)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_payload(payload, request=request)


def test_resealed_payload_cannot_authorize_collection_execution():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    payload["collection_execution_authorized"] = True
    _reseal_payload(payload)

    with pytest.raises(ValueError, match="collection_execution_authorized=false"):
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
            now="2026-09-28T14:02:00Z",
        )

    assert report["signature_verified"] is True
    assert report["trust_root_digest_matches"] is True
    assert report["human_collection_authorization_verified"] is True
    assert report["automatic_stop_required"] is True
    assert report["fresh_collection_readiness_recheck_required"] is True
    assert report["collection_execution_authorized"] is False
    assert report["paper_timer_enable_authorized"] is False
    assert report["live_capital_authorized"] is False


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
                now="2026-09-28T14:02:00Z",
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
                now="2026-09-28T14:02:00Z",
            )


def test_expired_collection_authorization_fails_closed():
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
                now="2026-09-28T14:06:00Z",
            )


def test_signature_does_not_survive_resealed_payload_tampering():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, request_path = _payload(root)
        signature, allowed, allowed_sha = _sign(root, payload)

        tampered = copy.deepcopy(payload)
        tampered["requested_collection_seconds"] -= 300
        _reseal_payload(tampered)
        payload_path.write_text(json.dumps(tampered), encoding="utf-8")

        with pytest.raises(ValueError, match="binding mismatch"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=request_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-28T14:02:00Z",
            )


def test_signed_collection_tool_has_no_timer_or_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "systemctl" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"collection_execution_authorized": False' in source
    assert '"new_market_entry_authorized": False' in source
    assert '"recurring_paper_automation_authorized": False' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
