from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase8_recursive_reentry_checkpoint_v25_signing_material_archive.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_phase8_recursive_reentry_checkpoint_v25_signing_material_archive",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: dict) -> str:
    payload = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(payload)
    return _sha(payload)


class _RequestModule:
    @staticmethod
    def validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_request(
        value,
    ):
        assert isinstance(value, dict)


class _FakeSigner:
    SIGNATURE_NAMESPACE = "pio-test-namespace"

    @staticmethod
    def _load_request(source):
        assert source == ROOT
        return _RequestModule

    @staticmethod
    def validate_payload(payload, request=None):
        assert isinstance(payload, dict)
        assert isinstance(request, dict)

    @staticmethod
    def validate_verification(value):
        assert isinstance(value, dict)

    @staticmethod
    def emit_signing_bytes(*, source_tree, request_path, payload_path):
        payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
        return MODULE._canonical_bytes(payload)

    @staticmethod
    def _verify_signature(*, payload, signature_path, allowed_signers_path):
        return (
            _sha(Path(signature_path).read_bytes()),
            _sha(Path(allowed_signers_path).read_bytes()),
        )


class _FakeBundle:
    @staticmethod
    def validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle(
        value,
    ):
        assert isinstance(value, dict)


class _FakeSession:
    @staticmethod
    def validate_phase8_recursive_reentry_checkpoint_v25_operator_session_archive(
        value,
    ):
        assert isinstance(value, dict)


def _fake_modules():
    return {
        "signer": _FakeSigner,
        "bundle": _FakeBundle,
        "session": _FakeSession,
    }


def _fixtures(root: Path):
    request = {"request_sha256": "1" * 64}
    request_path = root / "request-v25.json"
    request_file_sha = _write_json(request_path, request)

    payload_identity = {
        "approver_principal": "operator@example",
        "approval_id": "11111111-1111-4111-8111-111111111111",
        "issued_at": "2026-10-01T19:00:00Z",
        "expires_at": "2026-10-01T19:15:00Z",
        "authorization_scope": "ONE_PAIR_EVIDENCE_TICK",
    }
    payload = {
        **payload_identity,
        "payload_sha256": _sha(MODULE._canonical_bytes(payload_identity)),
    }
    payload_path = root / "signed-payload-v25.json"
    payload_file_sha = _write_json(payload_path, payload)

    signing_bytes = MODULE._canonical_bytes(payload)
    signing_path = root / "signing-bytes-v25"
    signing_path.write_bytes(signing_bytes)

    signature_path = root / "signature"
    signature_path.write_bytes(b"detached-signature")
    signature_sha = _sha(signature_path.read_bytes())

    allowed_path = root / "allowed_signers"
    allowed_path.write_bytes(b"operator@example namespaces ssh-ed25519 AAAATEST\n")
    allowed_sha = _sha(allowed_path.read_bytes())

    verification = {
        **payload_identity,
        "approval_payload_sha256": payload["payload_sha256"],
        "approval_signature_sha256": signature_sha,
        "allowed_signers_sha256": allowed_sha,
        "verification_sha256": "2" * 64,
        "paper_supervisor_tick_executed": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    verification_path = root / "signed-authorization-verification-v25.json"
    verification_file_sha = _write_json(verification_path, verification)

    bundle = {
        "artifact_file_sha256": {
            "request": request_file_sha,
            "signed_authorization_verification": verification_file_sha,
        },
        "approval_payload_sha256": payload["payload_sha256"],
        "approval_signature_sha256": signature_sha,
        "allowed_signers_sha256": allowed_sha,
        "bundle_sha256": "3" * 64,
    }
    bundle_path = root / "evidence-bundle-v25.json"
    bundle_file_sha = _write_json(bundle_path, bundle)

    session = {
        "core_artifact_file_sha256": {
            "request": request_file_sha,
            "signed_authorization_verification": verification_file_sha,
        },
        "evidence_bundle_sha256": bundle["bundle_sha256"],
        "evidence_bundle_file_sha256": bundle_file_sha,
        "session_archive_sha256": "4" * 64,
    }
    session_path = root / "operator-session-archive-v25.json"
    _write_json(session_path, session)

    return {
        "request": request_path,
        "payload": payload_path,
        "signing": signing_path,
        "signature": signature_path,
        "allowed": allowed_path,
        "verification": verification_path,
        "bundle": bundle_path,
        "session": session_path,
    }, request, payload, verification, bundle, session


def _build(monkeypatch):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    paths, request, payload, verification, bundle, session = _fixtures(root)
    monkeypatch.setattr(MODULE, "_load_reviewed", lambda source: _fake_modules())

    def run():
        return MODULE.build_phase8_recursive_reentry_checkpoint_v25_signing_material_archive(
            source_tree=ROOT,
            request_path=paths["request"],
            payload_path=paths["payload"],
            signing_bytes_path=paths["signing"],
            signature_path=paths["signature"],
            allowed_signers_path=paths["allowed"],
            signed_verification_path=paths["verification"],
            evidence_bundle_path=paths["bundle"],
            session_archive_path=paths["session"],
        )

    return temp, paths, request, payload, verification, bundle, session, run


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["signing_material_archive_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_signing_archive_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_reviewed_signing_archive_modules_are_loadable():
    modules = MODULE._load_reviewed(ROOT)

    assert callable(modules["signer"].validate_payload)
    assert callable(modules["signer"].validate_verification)
    assert callable(modules["signer"].emit_signing_bytes)
    assert callable(modules["signer"]._verify_signature)
    assert callable(
        modules["bundle"].validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle
    )
    assert callable(
        modules["session"].validate_phase8_recursive_reentry_checkpoint_v25_operator_session_archive
    )


def test_builds_non_reusable_historical_signing_archive(monkeypatch):
    temp, _, _, payload, verification, bundle, session, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["payload_sha256"] == payload["payload_sha256"]
    assert report["verification_sha256"] == verification["verification_sha256"]
    assert report["evidence_bundle_sha256"] == bundle["bundle_sha256"]
    assert report["session_archive_sha256"] == session["session_archive_sha256"]
    assert report["signing_bytes_exact"] is True
    assert report["cryptographic_signature_verified"] is True
    assert report["trust_root_digest_verified"] is True
    assert report["signing_material_archive_ready"] is True
    assert report["input_files_stable_during_archive"] is True
    assert report["archive_read_only"] is True
    assert report["historical_authorization_only"] is True
    assert report["authorization_currently_reusable"] is False
    assert report["authorization_validity_rechecked"] is False
    assert report["current_database_revalidation_performed"] is False
    assert report["next_action_authorized"] is False
    assert report["future_checkpoint_refresh_authorized"] is False
    assert report["paper_supervisor_tick_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["phase8_promotion_authorized"] is False
    MODULE.validate_phase8_recursive_reentry_checkpoint_v25_signing_material_archive(
        report
    )


def test_signing_bytes_drift_fails_closed(monkeypatch):
    temp, paths, _, _, _, _, _, run = _build(monkeypatch)
    try:
        paths["signing"].write_bytes(b"not-the-reviewed-signing-bytes")
        with pytest.raises(
            ValueError,
            match="signing bytes do not match reviewed payload",
        ):
            run()
    finally:
        temp.cleanup()


def test_signature_digest_drift_fails_closed(monkeypatch):
    temp, paths, _, _, verification, _, _, run = _build(monkeypatch)
    try:
        verification["approval_signature_sha256"] = "0" * 64
        _write_json(paths["verification"], verification)
        with pytest.raises(
            ValueError,
            match="signature/verification digest mismatch",
        ):
            run()
    finally:
        temp.cleanup()


def test_trust_root_digest_drift_fails_closed(monkeypatch):
    temp, paths, _, _, _, bundle, _, run = _build(monkeypatch)
    try:
        bundle["allowed_signers_sha256"] = "0" * 64
        _write_json(paths["bundle"], bundle)
        with pytest.raises(
            ValueError,
            match="trust-root/bundle digest mismatch",
        ):
            run()
    finally:
        temp.cleanup()


def test_request_file_drift_fails_closed(monkeypatch):
    temp, paths, request, _, _, _, _, run = _build(monkeypatch)
    try:
        request["extra"] = "drift"
        _write_json(paths["request"], request)
        with pytest.raises(
            ValueError,
            match="request file not bound to evidence bundle",
        ):
            run()
    finally:
        temp.cleanup()


def test_session_bundle_binding_drift_fails_closed(monkeypatch):
    temp, paths, _, _, _, _, session, run = _build(monkeypatch)
    try:
        session["evidence_bundle_sha256"] = "0" * 64
        _write_json(paths["session"], session)
        with pytest.raises(
            ValueError,
            match="bundle/session archive digest mismatch",
        ):
            run()
    finally:
        temp.cleanup()


def test_input_drift_during_archive_fails_closed(monkeypatch):
    temp, _, _, _, _, _, _, run = _build(monkeypatch)
    original = MODULE._regular_bytes
    seen = {"checkpoint v25 detached signature": 0}

    def drifting(path, *, label):
        result = original(path, label=label)
        if label == "checkpoint v25 detached signature":
            seen[label] += 1
            if seen[label] == 2:
                return result[0], result[1], "0" * 64
        return result

    monkeypatch.setattr(MODULE, "_regular_bytes", drifting)
    try:
        with pytest.raises(
            ValueError,
            match="detached signature changed during signing archive",
        ):
            run()
    finally:
        temp.cleanup()


def test_symlinked_signature_is_rejected(monkeypatch):
    temp, paths, _, _, _, _, _, _ = _build(monkeypatch)
    try:
        actual = paths["signature"].with_name("signature.actual")
        actual.write_bytes(paths["signature"].read_bytes())
        paths["signature"].unlink()
        paths["signature"].symlink_to(actual)
        with pytest.raises(ValueError, match="must not be a symlink"):
            MODULE.build_phase8_recursive_reentry_checkpoint_v25_signing_material_archive(
                source_tree=ROOT,
                request_path=paths["request"],
                payload_path=paths["payload"],
                signing_bytes_path=paths["signing"],
                signature_path=paths["signature"],
                allowed_signers_path=paths["allowed"],
                signed_verification_path=paths["verification"],
                evidence_bundle_path=paths["bundle"],
                session_archive_path=paths["session"],
            )
    finally:
        temp.cleanup()


def test_resealed_archive_cannot_claim_authorization_reusable(monkeypatch):
    temp, _, _, _, _, _, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["authorization_currently_reusable"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="authorization_currently_reusable=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_signing_material_archive(
            report
        )


def test_resealed_archive_cannot_authorize_live_submit(monkeypatch):
    temp, _, _, _, _, _, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["live_submit_authorized"] = True
    _reseal(report)
    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_signing_material_archive(
            report
        )


def test_signing_archive_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "sqlite3.connect" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"authorization_currently_reusable": False' in source
    assert '"authorization_validity_rechecked": False' in source
    assert '"current_database_revalidation_performed": False' in source
    assert '"future_checkpoint_refresh_authorized": False' in source
    assert '"next_action_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"phase8_promotion_authorized": False' in source
