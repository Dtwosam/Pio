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
    / "build_phase8_paper_challenger_transition_signed_authorization.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_paper_challenger_transition_signed_authorization",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

APPROVAL_ID = "11111111-2222-4333-8444-555555555555"


def _request() -> dict:
    return {
        "request_sha256": "a" * 64,
        "source_post_audit_sha256": "b" * 64,
        "source_execution_receipt_sha256": "c" * 64,
        "source_continuation_request_sha256": "d" * 64,
        "source_signed_authorization_verification_sha256": "e" * 64,
        "pio_database_sha256": "f" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "research_artifacts_sha256": "1" * 64,
        "research_artifact_count": 2,
        "debt_type": "PAPER_CHALLENGER_START_REQUIRED",
        "scope": "challenger-1",
        "active_cycle_id": "cycle-1",
        "model_id": "challenger-1",
        "active_cycle_status": "OFFLINE_QUALIFIED",
        "challenger_model_status": "OFFLINE_QUALIFIED",
        "reason": "paper transition is ready",
        "suggested_command": (
            "pio ml-start-paper --model-id challenger-1"
        ),
        "request_ready": True,
        "explicit_human_authorization_required": True,
        "fresh_post_audit_recheck_required": True,
        "exact_cycle_model_binding_required": True,
        "cycle_sync_required_after_model_transition": True,
        "paper_challenger_transition_authorization_present": False,
        "paper_challenger_transition_authorized": False,
        "paper_challenger_transition_executed": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_execution_authorized": False,
        "phase8_promotion_authorized": False,
    }


class _FakeRequest:
    @staticmethod
    def validate_phase8_paper_challenger_transition_request(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _payload(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp) / "request.json", _request())
        return MODULE.build_payload(
            source_tree=ROOT,
            request_path=path,
            approver_principal="ops@example.com",
            issued_at="2026-09-29T21:30:00Z",
            ttl_seconds=300,
            approval_id=APPROVAL_ID,
        )


def _reseal_verification(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.VERIFICATION_FIELDS
    }
    report["verification_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_paper_transition_request_is_exactly_pinned():
    path = ROOT / MODULE.REQUEST_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.REQUEST_TOOL
    ]


def test_payload_binds_exact_cycle_model_and_state(monkeypatch):
    payload = _payload(monkeypatch)

    assert payload["request_sha256"] == "a" * 64
    assert payload["source_post_audit_sha256"] == "b" * 64
    assert payload["source_execution_receipt_sha256"] == "c" * 64
    assert payload["source_continuation_request_sha256"] == "d" * 64
    assert (
        payload["source_signed_authorization_verification_sha256"]
        == "e" * 64
    )
    assert payload["active_cycle_id"] == "cycle-1"
    assert payload["model_id"] == "challenger-1"
    assert payload["scope"] == "challenger-1"
    assert payload["active_cycle_status"] == "OFFLINE_QUALIFIED"
    assert payload["challenger_model_status"] == "OFFLINE_QUALIFIED"
    assert payload["human_authorization_intent"] is True
    assert payload["fresh_post_audit_recheck_required"] is True
    assert payload["exact_cycle_model_binding_required"] is True
    assert payload["cycle_sync_required_after_model_transition"] is True
    assert payload["paper_challenger_transition_executed"] is False
    assert payload["paper_evidence_collection_authorized"] is False
    assert payload["paper_trading_authorized"] is False
    assert payload["live_submit_authorized"] is False
    assert payload["new_live_capital_authorized"] is False
    assert payload["phase8_execution_authorized"] is False
    assert payload["phase8_promotion_authorized"] is False


def test_payload_rejects_excessive_ttl(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp) / "request.json", _request())
        with pytest.raises(ValueError, match="ttl_seconds"):
            MODULE.build_payload(
                source_tree=ROOT,
                request_path=path,
                approver_principal="ops@example.com",
                issued_at="2026-09-29T21:30:00Z",
                ttl_seconds=MODULE.MAX_TTL_SECONDS + 1,
                approval_id=APPROVAL_ID,
            )


def test_payload_rejects_cycle_model_binding_drift(monkeypatch):
    payload = _payload(monkeypatch)
    request = _request()
    payload["active_cycle_id"] = "cycle-2"
    identity = {
        field: payload[field]
        for field in MODULE.PAYLOAD_FIELDS
    }
    payload["payload_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    with pytest.raises(ValueError, match="binding mismatch"):
        MODULE.validate_payload(payload, request=request)


def test_verification_checks_signature_trust_root_and_expiry(monkeypatch):
    request = _request()
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = _write(root / "request.json", request)
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-29T21:30:00Z",
            ttl_seconds=300,
            approval_id=APPROVAL_ID,
        )
        payload_path = _write(root / "payload.json", payload)
        signature = root / "signature"
        allowed = root / "allowed"
        signature.write_bytes(b"sig")
        allowed.write_bytes(b"allowed")
        allowed_sha = hashlib.sha256(b"allowed").hexdigest()

        monkeypatch.setattr(
            MODULE,
            "_verify_signature",
            lambda **kwargs: (
                hashlib.sha256(b"sig").hexdigest(),
                allowed_sha,
            ),
        )

        report = MODULE.verify_authorization(
            source_tree=ROOT,
            request_path=request_path,
            payload_path=payload_path,
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256=allowed_sha,
            now="2026-09-29T21:34:00Z",
        )

        assert report[
            "human_paper_challenger_transition_authorization_verified"
        ] is True
        assert report["approval_not_expired"] is True
        assert report["active_cycle_id"] == "cycle-1"
        assert report["model_id"] == "challenger-1"
        assert report["cycle_sync_required_after_model_transition"] is True
        assert report["paper_challenger_transition_executed"] is False
        assert report["paper_trading_authorized"] is False
        assert report["live_submit_authorized"] is False

        with pytest.raises(ValueError, match="expired"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=request_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-29T21:36:00Z",
            )


def test_verification_rejects_wrong_trust_root(monkeypatch):
    request = _request()
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = _write(root / "request.json", request)
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-29T21:30:00Z",
            approval_id=APPROVAL_ID,
        )
        payload_path = _write(root / "payload.json", payload)
        signature = root / "signature"
        allowed = root / "allowed"
        signature.write_bytes(b"sig")
        allowed.write_bytes(b"allowed")
        allowed_sha = hashlib.sha256(b"allowed").hexdigest()
        monkeypatch.setattr(
            MODULE,
            "_verify_signature",
            lambda **kwargs: (
                hashlib.sha256(b"sig").hexdigest(),
                allowed_sha,
            ),
        )

        with pytest.raises(ValueError, match="trust-root"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=request_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256="9" * 64,
                now="2026-09-29T21:34:00Z",
            )


def test_resealed_verification_cannot_claim_transition_executed(
    monkeypatch,
):
    request = _request()
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = _write(root / "request.json", request)
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-29T21:30:00Z",
            approval_id=APPROVAL_ID,
        )
        payload_path = _write(root / "payload.json", payload)
        signature = root / "signature"
        allowed = root / "allowed"
        signature.write_bytes(b"sig")
        allowed.write_bytes(b"allowed")
        allowed_sha = hashlib.sha256(b"allowed").hexdigest()
        monkeypatch.setattr(
            MODULE,
            "_verify_signature",
            lambda **kwargs: (
                hashlib.sha256(b"sig").hexdigest(),
                allowed_sha,
            ),
        )
        report = MODULE.verify_authorization(
            source_tree=ROOT,
            request_path=request_path,
            payload_path=payload_path,
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256=allowed_sha,
            now="2026-09-29T21:34:00Z",
        )

    report["paper_challenger_transition_executed"] = True
    _reseal_verification(report)
    with pytest.raises(
        ValueError,
        match="paper_challenger_transition_executed=false",
    ):
        MODULE.validate_verification(report)


def test_resealed_verification_cannot_authorize_paper_trading(
    monkeypatch,
):
    request = _request()
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = _write(root / "request.json", request)
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-29T21:30:00Z",
            approval_id=APPROVAL_ID,
        )
        payload_path = _write(root / "payload.json", payload)
        signature = root / "signature"
        allowed = root / "allowed"
        signature.write_bytes(b"sig")
        allowed.write_bytes(b"allowed")
        allowed_sha = hashlib.sha256(b"allowed").hexdigest()
        monkeypatch.setattr(
            MODULE,
            "_verify_signature",
            lambda **kwargs: (
                hashlib.sha256(b"sig").hexdigest(),
                allowed_sha,
            ),
        )
        report = MODULE.verify_authorization(
            source_tree=ROOT,
            request_path=request_path,
            payload_path=payload_path,
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256=allowed_sha,
            now="2026-09-29T21:34:00Z",
        )

    report["paper_trading_authorized"] = True
    _reseal_verification(report)
    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_verification(report)


def test_paper_transition_signer_has_no_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "start_paper_challenger(" not in source
    assert "start_model_paper_challenger(" not in source
    assert "sync_retraining_cycle(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_challenger_transition_executed": False' in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
