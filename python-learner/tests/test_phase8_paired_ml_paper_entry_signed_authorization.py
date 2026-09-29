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
    / "build_phase8_paired_ml_paper_entry_signed_authorization.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_paired_ml_paper_entry_signed_authorization",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

APPROVAL_ID = "11111111-2222-4333-8444-555555555555"


def _request() -> dict:
    return {
        "request_sha256": "a" * 64,
        "source_account_readiness_sha256": "b" * 64,
        "paired_entry_input_verification_sha256": "c" * 64,
        "source_post_audit_sha256": "d" * 64,
        "source_paper_evidence_input_verification_sha256": "e" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "f" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "pair_id": "pair-1",
        "pool_address": "pool-1",
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "required_pair_cash_quote": 2010.0,
        "incumbent_position_id": "pair-1-incumbent",
        "challenger_position_id": "pair-1-challenger",
        "incumbent_event_key": "pair-1-incumbent-enter",
        "challenger_event_key": "pair-1-challenger-enter",
        "reason": "open one fair paired PAPER comparison",
        "request_ready": True,
        "explicit_human_authorization_required": True,
        "fresh_account_readiness_recheck_required": True,
        "fresh_model_inference_readiness_required": True,
        "same_candidate_frame_required": True,
        "same_decision_snapshot_required": True,
        "equal_capital_required": True,
        "atomic_pair_open_required": True,
        "post_pair_audit_required": True,
        "paper_account_creation_authorized": False,
        "paper_pair_entry_authorization_present": False,
        "paper_pair_entry_authorized": False,
        "paper_pair_entry_executed": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_promotion_authorized": False,
    }


class _FakeRequest:
    @staticmethod
    def validate_phase8_paired_ml_paper_entry_request(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _payload(monkeypatch, *, request: dict | None = None):
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequest,
    )
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    request_path = _write(
        root / "request.json",
        request if request is not None else _request(),
    )
    value = MODULE.build_payload(
        source_tree=ROOT,
        request_path=request_path,
        approver_principal="ops@example.com",
        issued_at="2026-09-30T00:30:00Z",
        ttl_seconds=300,
        approval_id=APPROVAL_ID,
    )
    return temp, root, request_path, value


def _reseal_verification(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.VERIFICATION_FIELDS
    }
    report["verification_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_pair_request_is_exactly_pinned():
    path = ROOT / MODULE.REQUEST_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.REQUEST_TOOL
    ]


def test_payload_binds_exact_pair_and_economics(monkeypatch):
    temp, _, _, payload = _payload(monkeypatch)
    try:
        assert payload["request_sha256"] == "a" * 64
        assert payload["active_cycle_id"] == "cycle-1"
        assert payload["incumbent_model_id"] == "champion-1"
        assert payload["challenger_model_id"] == "challenger-1"
        assert payload["account_id"] == "paper-1"
        assert payload["pair_id"] == "pair-1"
        assert payload["capital_quote"] == 1000.0
        assert payload["entry_cost_quote"] == 5.0
        assert payload["required_pair_cash_quote"] == 2010.0
        assert payload["human_authorization_intent"] is True
        assert payload["fresh_account_readiness_recheck_required"] is True
        assert payload["fresh_model_inference_readiness_required"] is True
        assert payload["same_candidate_frame_required"] is True
        assert payload["same_decision_snapshot_required"] is True
        assert payload["equal_capital_required"] is True
        assert payload["atomic_pair_open_required"] is True
        assert payload["post_pair_audit_required"] is True
        assert payload["paper_account_creation_authorized"] is False
        assert payload["paper_pair_entry_executed"] is False
        assert payload["paper_evidence_collection_authorized"] is False
        assert payload["paper_trading_authorized"] is False
        assert payload["live_submit_authorized"] is False
        assert payload["new_live_capital_authorized"] is False
        assert payload["phase8_promotion_authorized"] is False
    finally:
        temp.cleanup()


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
                issued_at="2026-09-30T00:30:00Z",
                ttl_seconds=MODULE.MAX_TTL_SECONDS + 1,
                approval_id=APPROVAL_ID,
            )


def test_payload_rejects_pair_cash_mismatch(monkeypatch):
    request = _request()
    request["required_pair_cash_quote"] = 2000.0

    with pytest.raises(ValueError, match="pair cash mismatch"):
        temp, _, _, _ = _payload(monkeypatch, request=request)
        temp.cleanup()


def test_payload_rejects_same_model_identity(monkeypatch):
    request = _request()
    request["incumbent_model_id"] = "challenger-1"

    with pytest.raises(ValueError, match="model identities must differ"):
        temp, _, _, _ = _payload(monkeypatch, request=request)
        temp.cleanup()


def test_payload_rejects_binding_drift(monkeypatch):
    temp, _, _, payload = _payload(monkeypatch)
    try:
        request = _request()
        payload["pair_id"] = "pair-2"
        identity = {
            field: payload[field]
            for field in MODULE.PAYLOAD_FIELDS
        }
        payload["payload_sha256"] = hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest()

        with pytest.raises(ValueError, match="pair_id binding mismatch"):
            MODULE.validate_payload(payload, request=request)
    finally:
        temp.cleanup()


def test_verification_checks_signature_trust_root_and_expiry(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = _write(root / "request.json", _request())
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-30T00:30:00Z",
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
            now="2026-09-30T00:34:00Z",
        )

        assert report[
            "human_paired_paper_entry_authorization_verified"
        ] is True
        assert report["approval_not_expired"] is True
        assert report["pair_id"] == "pair-1"
        assert report["paper_pair_entry_executed"] is False
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
                now="2026-09-30T00:36:00Z",
            )


def test_verification_rejects_wrong_trust_root(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = _write(root / "request.json", _request())
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-30T00:30:00Z",
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
                now="2026-09-30T00:34:00Z",
            )


def _verified_report(monkeypatch) -> dict:
    monkeypatch.setattr(
        MODULE,
        "_load_request_module",
        lambda source: _FakeRequest,
    )
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    request_path = _write(root / "request.json", _request())
    payload = MODULE.build_payload(
        source_tree=ROOT,
        request_path=request_path,
        approver_principal="ops@example.com",
        issued_at="2026-09-30T00:30:00Z",
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
        now="2026-09-30T00:34:00Z",
    )
    temp.cleanup()
    return report


def test_resealed_verification_cannot_claim_pair_execution(monkeypatch):
    report = _verified_report(monkeypatch)
    report["paper_pair_entry_executed"] = True
    _reseal_verification(report)

    with pytest.raises(
        ValueError,
        match="paper_pair_entry_executed=false",
    ):
        MODULE.validate_verification(report)


def test_resealed_verification_cannot_authorize_paper_trading(monkeypatch):
    report = _verified_report(monkeypatch)
    report["paper_trading_authorized"] = True
    _reseal_verification(report)

    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_verification(report)


def test_resealed_verification_cannot_authorize_account_creation(
    monkeypatch,
):
    report = _verified_report(monkeypatch)
    report["paper_account_creation_authorized"] = True
    _reseal_verification(report)

    with pytest.raises(
        ValueError,
        match="paper_account_creation_authorized=false",
    ):
        MODULE.validate_verification(report)


def test_signer_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "open_paired_ml_paper_entries(" not in source
    assert "create_paper_account(" not in source
    assert "open_paper_position(" not in source
    assert "run_paper_supervisor(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_pair_entry_executed": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
