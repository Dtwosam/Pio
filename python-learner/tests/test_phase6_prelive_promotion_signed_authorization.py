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
    / "build_phase6_prelive_promotion_signed_authorization.py"
)
REQUEST_TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase6_prelive_promotion_request.py"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load(TOOL, "phase6_promotion_signed_authorization_test")
REQUEST = _load(REQUEST_TOOL, "phase6_promotion_request_for_signed_test")


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
        "phase6_evidence_status_sha256": "1" * 64,
        "phase5_post_promotion_audit_sha256": "2" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "execution_database_path": "/var/lib/pio/execution.db",
        "pio_database_sha256": "3" * 64,
        "execution_database_sha256": "4" * 64,
        "phase6_criteria_sha256": "5" * 64,
        "phase6_report_sha256": "6" * 64,
        "passed_enter_intents": 10,
        "distinct_pools": 2,
        "blocked_intents": 2,
        "postsimulation_intents": 0,
        "invalid_passed_intents": 0,
        "distinct_authorized_wallets": ["wallet-1"],
        "phase5_promoted": True,
        "phase6_promotion_ready": True,
        "phase6_reasons": [],
        "promotion_request_ready": True,
        "phase6_promotion_authorization_present": False,
        "phase6_promotion_persisted": False,
        "explicit_human_authorization_required": True,
        "fresh_phase6_promotion_recheck_required": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
    }
    request = {
        **identity,
        "request_sha256": hashlib.sha256(
            REQUEST._canonical_bytes(identity)
        ).hexdigest(),
    }
    REQUEST.validate_phase6_promotion_request(request)
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
        issued_at="2026-09-28T18:00:00Z",
        ttl_seconds=300,
        approval_id="fedcba98-7654-4cba-8123-456789abcdef",
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


def test_signature_namespace_is_phase6_specific():
    assert MODULE.SIGNATURE_NAMESPACE == (
        "pio-phase6-prelive-promotion-authorization-v1"
    )
    assert "phase5" not in MODULE.SIGNATURE_NAMESPACE
    assert "collection" not in MODULE.SIGNATURE_NAMESPACE


def test_payload_binds_exact_phase6_corpus_but_does_not_authorize_live():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    assert payload["request_sha256"] == request["request_sha256"]
    assert (
        payload["phase6_evidence_status_sha256"]
        == request["phase6_evidence_status_sha256"]
    )
    assert payload["pio_database_sha256"] == request["pio_database_sha256"]
    assert (
        payload["execution_database_sha256"]
        == request["execution_database_sha256"]
    )
    assert payload["passed_enter_intents"] == 10
    assert payload["distinct_authorized_wallets"] == ["wallet-1"]
    assert payload["human_authorization_intent"] is True
    assert payload["fresh_phase6_promotion_recheck_required"] is True
    assert payload["phase6_promotion_persisted"] is False
    assert payload["controlled_live_authorized"] is False
    assert payload["live_submit_authorized"] is False
    assert payload["live_capital_authorized"] is False


def test_emit_signing_bytes_matches_verifier_bytes():
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


def test_resealed_payload_cannot_claim_phase6_persisted():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    payload["phase6_promotion_persisted"] = True
    _reseal_payload(payload)

    with pytest.raises(ValueError, match="phase6_promotion_persisted=false"):
        MODULE.validate_payload(payload, request=request)


def test_resealed_payload_cannot_authorize_live_submit():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, request_path = _payload(root)
        request = json.loads(request_path.read_text(encoding="utf-8"))

    payload["live_submit_authorized"] = True
    _reseal_payload(payload)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
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
            now="2026-09-28T18:02:00Z",
        )

    assert report["signature_verified"] is True
    assert report["trust_root_digest_matches"] is True
    assert report["human_phase6_promotion_authorization_verified"] is True
    assert report["fresh_phase6_promotion_recheck_required"] is True
    assert report["phase6_promotion_persisted"] is False
    assert report["controlled_live_authorized"] is False
    assert report["live_submit_authorized"] is False
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
                now="2026-09-28T18:02:00Z",
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
                now="2026-09-28T18:02:00Z",
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
                now="2026-09-28T18:06:00Z",
            )


def test_signature_does_not_survive_resealed_payload_tampering():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, request_path = _payload(root)
        signature, allowed, allowed_sha = _sign(root, payload)

        tampered = copy.deepcopy(payload)
        tampered["passed_enter_intents"] = 999
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
                now="2026-09-28T18:02:00Z",
            )


def test_signed_phase6_tool_has_no_persistence_or_live_executor():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "systemctl" not in source
    assert "persist_phase6_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert "shell=True" not in source
    assert '"phase6_promotion_persisted": False' in source
    assert '"controlled_live_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
