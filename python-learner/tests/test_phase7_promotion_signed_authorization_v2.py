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
    / "build_phase7_promotion_signed_authorization_v2.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_promotion_signed_authorization_v2",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


APPROVAL_ID = "01234567-89ab-4def-8123-456789abcdef"
ISSUED = "2026-09-29T18:00:00Z"
ALLOWED_SHA = "f" * 64
SIGNATURE_SHA = "e" * 64


def _request() -> dict:
    return {
        "request_sha256": "a" * 64,
        "final_handoff_sha256": "b" * 64,
        "phase7_evidence_status_sha256": "c" * 64,
        "phase7_evidence_plan_sha256": "d" * 64,
        "pio_database_sha256": "1" * 64,
        "confirmed_receipts": 6,
        "failed_receipts": 0,
        "closed_positions": 3,
        "open_positions": 0,
        "distinct_closed_pools": 2,
        "valued_closed_positions": 3,
        "labeled_closed_positions": 3,
        "ledger_clean": True,
        "promotion_request_ready": True,
        "phase7_promotion_authorization_present": False,
        "phase7_promotion_persisted": False,
    }


class _FakeRequestModule:
    @staticmethod
    def validate_phase7_promotion_request_v2(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _payload(monkeypatch, *, request_override=None, ttl=300):
    request = _request()
    if request_override:
        request.update(request_override)
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequestModule,
    )
    with tempfile.TemporaryDirectory() as tmp:
        request_path = _write(Path(tmp) / "request.json", request)
        return MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="operator@example.com",
            issued_at=ISSUED,
            ttl_seconds=ttl,
            approval_id=APPROVAL_ID,
        )


def _verify(monkeypatch, *, now="2026-09-29T18:04:00Z", allowed_sha=ALLOWED_SHA):
    request = _request()
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequestModule,
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = _write(root / "request.json", request)
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="operator@example.com",
            issued_at=ISSUED,
            ttl_seconds=300,
            approval_id=APPROVAL_ID,
        )
        payload_path = _write(root / "payload.json", payload)
        signature = root / "approval.sig"
        signature.write_bytes(b"signature")
        allowed = root / "allowed_signers"
        allowed.write_bytes(b"allowed")

        monkeypatch.setattr(
            MODULE,
            "_verify_signature",
            lambda **kwargs: (SIGNATURE_SHA, allowed_sha),
        )

        return MODULE.verify_authorization(
            source_tree=ROOT,
            request_path=request_path,
            payload_path=payload_path,
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256=ALLOWED_SHA,
            now=now,
        )


def _reseal_payload(payload: dict) -> None:
    identity = {field: payload[field] for field in MODULE.PAYLOAD_FIELDS}
    payload["payload_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def _reseal_verification(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.VERIFICATION_FIELDS}
    report["verification_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_request_dependency_is_exactly_pinned():
    path = ROOT / MODULE.REQUEST_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.REQUEST_TOOL
    ]


def test_payload_binds_exact_promotion_request(monkeypatch):
    payload = _payload(monkeypatch)

    assert payload["signature_namespace"] == MODULE.SIGNATURE_NAMESPACE
    assert payload["decision"] == MODULE.DECISION
    assert payload["authorization_scope"] == MODULE.AUTHORIZATION_SCOPE
    assert payload["request_sha256"] == "a" * 64
    assert payload["closed_positions"] == 3
    assert payload["valued_closed_positions"] == 3
    assert payload["labeled_closed_positions"] == 3
    assert payload["human_authorization_intent"] is True
    assert payload["fresh_phase7_promotion_recheck_required"] is True
    assert payload["phase7_promotion_persisted"] is False
    assert payload["transaction_submission_authorized"] is False
    assert payload["new_live_capital_authorized"] is False


def test_payload_ttl_is_bounded(monkeypatch):
    with pytest.raises(ValueError, match="ttl_seconds"):
        _payload(monkeypatch, ttl=MODULE.MAX_TTL_SECONDS + 1)


def test_resealed_payload_cannot_authorize_submission(monkeypatch):
    payload = _payload(monkeypatch)
    payload["transaction_submission_authorized"] = True
    _reseal_payload(payload)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_payload(payload, request=_request())


def test_verification_accepts_exact_signature_and_trust_root(monkeypatch):
    report = _verify(monkeypatch)

    assert report["signature_verified"] is True
    assert report["trust_root_digest_matches"] is True
    assert report["approval_not_expired"] is True
    assert report["human_phase7_promotion_authorization_verified"] is True
    assert report["approval_signature_sha256"] == SIGNATURE_SHA
    assert report["allowed_signers_sha256"] == ALLOWED_SHA
    assert report["phase7_promotion_persisted"] is False
    assert report["transaction_submission_authorized"] is False


def test_expired_authorization_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="expired"):
        _verify(monkeypatch, now="2026-09-29T18:05:01Z")


def test_allowed_signers_digest_mismatch_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="trust-root digest mismatch"):
        _verify(monkeypatch, allowed_sha="0" * 64)


def test_resealed_verification_cannot_claim_persistence(monkeypatch):
    report = _verify(monkeypatch)
    report["phase7_promotion_persisted"] = True
    _reseal_verification(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_persisted=false",
    ):
        MODULE.validate_verification(report)


def test_signed_authorization_has_no_persistence_or_live_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "persist_phase7_promotion" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert '"phase7_promotion_persisted": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
