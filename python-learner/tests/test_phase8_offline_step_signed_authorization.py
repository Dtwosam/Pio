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
    / "build_phase8_offline_step_signed_authorization.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_offline_step_signed_authorization",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


APPROVAL_ID = "11111111-2222-4333-8444-555555555555"


def _request() -> dict:
    return {
        "request_sha256": "a" * 64,
        "saved_phase8_handoff_sha256": "b" * 64,
        "pio_database_sha256": "c" * 64,
        "debt_type": "RETRAIN_OFFLINE_TRAIN_READY",
        "scope": "cycle-1",
        "request_ready": True,
        "offline_step_authorization_present": False,
        "offline_step_executed": False,
    }


class _FakeRequest:
    @staticmethod
    def validate_phase8_offline_step_request(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _payload(monkeypatch):
    request = _request()
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp) / "request.json", request)
        return MODULE.build_payload(
            source_tree=ROOT,
            request_path=path,
            approver_principal="ops@example.com",
            issued_at="2026-09-29T20:00:00Z",
            ttl_seconds=300,
            approval_id=APPROVAL_ID,
        )


def _reseal_verification(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.VERIFICATION_FIELDS}
    report["verification_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_request_tool_is_exactly_pinned():
    path = ROOT / MODULE.REQUEST_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.REQUEST_TOOL
    ]


def test_payload_binds_exact_offline_step_and_authorizes_nothing_else(
    monkeypatch,
):
    payload = _payload(monkeypatch)

    assert payload["request_sha256"] == "a" * 64
    assert payload["saved_phase8_handoff_sha256"] == "b" * 64
    assert payload["pio_database_sha256"] == "c" * 64
    assert payload["debt_type"] == "RETRAIN_OFFLINE_TRAIN_READY"
    assert payload["scope"] == "cycle-1"
    assert payload["authorization_scope"] == MODULE.AUTHORIZATION_SCOPE
    assert payload["human_authorization_intent"] is True
    assert payload["fresh_phase8_handoff_recheck_required"] is True
    assert payload["offline_step_executed"] is False
    assert payload["paper_challenger_transition_authorized"] is False
    assert payload["paper_trading_authorized"] is False
    assert payload["live_submit_authorized"] is False
    assert payload["new_live_capital_authorized"] is False


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
                issued_at="2026-09-29T20:00:00Z",
                ttl_seconds=MODULE.MAX_TTL_SECONDS + 1,
                approval_id=APPROVAL_ID,
            )


def test_verification_checks_trust_root_and_expiry(monkeypatch):
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
            issued_at="2026-09-29T20:00:00Z",
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
            now="2026-09-29T20:04:00Z",
        )

        assert report["human_offline_step_authorization_verified"] is True
        assert report["approval_not_expired"] is True
        assert report["trust_root_digest_matches"] is True
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
                now="2026-09-29T20:06:00Z",
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
            issued_at="2026-09-29T20:00:00Z",
            approval_id=APPROVAL_ID,
        )
        payload_path = _write(root / "payload.json", payload)
        signature = root / "signature"
        allowed = root / "allowed"
        signature.write_bytes(b"sig")
        allowed.write_bytes(b"allowed")

        monkeypatch.setattr(
            MODULE,
            "_verify_signature",
            lambda **kwargs: (
                hashlib.sha256(b"sig").hexdigest(),
                hashlib.sha256(b"allowed").hexdigest(),
            ),
        )

        with pytest.raises(ValueError, match="trust-root digest mismatch"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=request_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256="f" * 64,
                now="2026-09-29T20:04:00Z",
            )


def test_resealed_verification_cannot_authorize_paper_trading(monkeypatch):
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
            issued_at="2026-09-29T20:00:00Z",
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
            now="2026-09-29T20:04:00Z",
        )
        report["paper_trading_authorized"] = True
        _reseal_verification(report)

        with pytest.raises(
            ValueError,
            match="paper_trading_authorized=false",
        ):
            MODULE.validate_verification(report)


def test_signed_authorization_has_no_step_or_paper_execution():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_phase8_evidence_step(" not in source
    assert "start_paper_challenger(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"offline_step_executed": False' in source
    assert '"paper_challenger_transition_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
