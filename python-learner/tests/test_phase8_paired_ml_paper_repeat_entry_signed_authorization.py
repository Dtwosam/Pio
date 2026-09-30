from __future__ import annotations

from datetime import datetime, timezone
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase8_paired_ml_paper_repeat_entry_signed_authorization.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_paired_ml_paper_repeat_entry_signed_authorization",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _request() -> dict:
    return {
        "request_sha256": "a" * 64,
        "source_repeat_account_readiness_sha256": "b" * 64,
        "repeat_entry_input_verification_sha256": "c" * 64,
        "source_final_evaluation_sha256": "d" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "e" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-1",
        "pair_id": "pair-2",
        "pool_address": "pool-2",
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "required_pair_cash_quote": 2010.0,
        "incumbent_position_id": "p8-pair-2-incumbent",
        "challenger_position_id": "p8-pair-2-challenger",
        "incumbent_event_key": "p8-pair-2:incumbent",
        "challenger_event_key": "p8-pair-2:challenger",
        "reason": "open one repeat equal-capital pair",
        "request_ready": True,
        "explicit_human_authorization_required": True,
        "fresh_account_readiness_recheck_required": True,
        "fresh_model_inference_readiness_required": True,
        "same_candidate_frame_required": True,
        "same_decision_snapshot_required": True,
        "equal_capital_required": True,
        "atomic_pair_open_required": True,
        "zero_open_positions_required": True,
        "previous_pair_closed_required": True,
        "post_pair_audit_required": True,
        "paper_pair_entry_authorization_present": False,
        "paper_pair_entry_authorized": False,
        "paper_pair_entry_executed": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
    }


class _FakeRequest:
    @staticmethod
    def validate_phase8_paired_ml_paper_repeat_entry_request(value):
        assert isinstance(value, dict)


class _FakeBaseSigner:
    @staticmethod
    def _is_hex_digest(value, length):
        return (
            isinstance(value, str)
            and len(value) == length
            and all(ch in "0123456789abcdef" for ch in value)
        )

    @staticmethod
    def _approval_id(raw):
        parsed = uuid.UUID(raw)
        normalized = str(parsed)
        if raw != normalized:
            raise ValueError("approval id is not canonical")
        return normalized

    @staticmethod
    def _principal(raw):
        if not isinstance(raw, str) or not raw:
            raise ValueError("principal is invalid")
        return raw

    @staticmethod
    def _canonical_utc(raw):
        if not isinstance(raw, str) or not raw.endswith("Z"):
            raise ValueError("timestamp must use UTC Z form")
        return datetime.strptime(
            raw,
            "%Y-%m-%dT%H:%M:%SZ",
        ).replace(tzinfo=timezone.utc)

    @staticmethod
    def _format_utc(value):
        return value.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    @staticmethod
    def _regular_file(path, *, label):
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_file():
            raise ValueError(f"{label} must be regular")
        return resolved

    @staticmethod
    def _sha256_path(path):
        return _sha(Path(path).read_bytes())

    @staticmethod
    def _ssh_keygen_path():
        return Path("/usr/bin/ssh-keygen")


def _write(path: Path, value) -> Path:
    if isinstance(value, (bytes, bytearray)):
        path.write_bytes(bytes(value))
    else:
        path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _setup(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (_FakeRequest, _FakeBaseSigner),
    )
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    request_path = _write(root / "request.json", _request())
    return temp, root, request_path


def _reseal_payload(payload: dict) -> None:
    identity = {
        field: payload[field]
        for field in MODULE.PAYLOAD_FIELDS
    }
    payload["payload_sha256"] = _sha(
        MODULE._canonical_bytes(identity)
    )


def _reseal_verification(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.VERIFICATION_FIELDS
    }
    report["verification_sha256"] = _sha(
        MODULE._canonical_bytes(identity)
    )


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_repeat_namespace_is_distinct_from_initial_pair_namespace():
    assert MODULE.SIGNATURE_NAMESPACE == (
        "pio-phase8-repeat-paired-ml-paper-entry-authorization-v1"
    )

    initial = ROOT / MODULE.BASE_SIGNER_TOOL
    source = initial.read_text(encoding="utf-8")
    assert MODULE.SIGNATURE_NAMESPACE not in source
    assert (
        'SIGNATURE_NAMESPACE = "pio-phase8-paired-ml-paper-entry-authorization-v1"'
        in source
    )


def test_build_payload_binds_previous_and_new_pair(monkeypatch):
    temp, _, request_path = _setup(monkeypatch)
    try:
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-30T09:00:00Z",
            ttl_seconds=300,
            approval_id="11111111-2222-4333-8444-555555555555",
        )
    finally:
        temp.cleanup()

    assert payload["previous_pair_id"] == "pair-1"
    assert payload["pair_id"] == "pair-2"
    assert payload["signature_namespace"] == MODULE.SIGNATURE_NAMESPACE
    assert payload["authorization_scope"] == MODULE.AUTHORIZATION_SCOPE
    assert payload["decision"] == MODULE.DECISION
    assert payload["zero_open_positions_required"] is True
    assert payload["previous_pair_closed_required"] is True
    assert payload["paper_pair_entry_executed"] is False
    assert payload["paper_trading_authorized"] is False
    assert payload["live_submit_authorized"] is False
    assert payload["continuous_promotion_authorized"] is False


def test_build_payload_rejects_excessive_ttl(monkeypatch):
    temp, _, request_path = _setup(monkeypatch)
    try:
        with pytest.raises(ValueError, match="ttl_seconds"):
            MODULE.build_payload(
                source_tree=ROOT,
                request_path=request_path,
                approver_principal="ops@example.com",
                issued_at="2026-09-30T09:00:00Z",
                ttl_seconds=MODULE.MAX_TTL_SECONDS + 1,
                approval_id=(
                    "11111111-2222-4333-8444-555555555555"
                ),
            )
    finally:
        temp.cleanup()


def test_resealed_payload_cannot_reuse_previous_pair(monkeypatch):
    temp, _, request_path = _setup(monkeypatch)
    try:
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-30T09:00:00Z",
            approval_id="11111111-2222-4333-8444-555555555555",
        )
        request = _request()
    finally:
        temp.cleanup()

    payload["pair_id"] = payload["previous_pair_id"]
    _reseal_payload(payload)
    with pytest.raises(ValueError, match="pair id was not advanced"):
        MODULE.validate_payload(
            payload,
            request=request,
            base_signer=_FakeBaseSigner,
        )


def test_resealed_payload_cannot_claim_live_or_paper_execution(
    monkeypatch,
):
    temp, _, request_path = _setup(monkeypatch)
    try:
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-30T09:00:00Z",
            approval_id="11111111-2222-4333-8444-555555555555",
        )
        request = _request()
    finally:
        temp.cleanup()

    payload["paper_pair_entry_executed"] = True
    _reseal_payload(payload)
    with pytest.raises(
        ValueError,
        match="paper_pair_entry_executed=false",
    ):
        MODULE.validate_payload(
            payload,
            request=request,
            base_signer=_FakeBaseSigner,
        )

    payload = MODULE.build_payload.__wrapped__ if False else None


def test_verify_authorization_checks_signature_trust_and_expiry(
    monkeypatch,
):
    temp, root, request_path = _setup(monkeypatch)
    try:
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-30T09:00:00Z",
            ttl_seconds=300,
            approval_id="11111111-2222-4333-8444-555555555555",
        )
        payload_path = _write(root / "payload.json", payload)
        signature_path = _write(root / "signature", b"signature-bytes")
        allowed_path = _write(root / "allowed", b"allowed-signers")
        signature_sha = _sha(signature_path.read_bytes())
        allowed_sha = _sha(allowed_path.read_bytes())

        monkeypatch.setattr(
            MODULE,
            "_verify_signature",
            lambda **kwargs: (signature_sha, allowed_sha),
        )

        report = MODULE.verify_authorization(
            source_tree=ROOT,
            request_path=request_path,
            payload_path=payload_path,
            signature_path=signature_path,
            allowed_signers_path=allowed_path,
            expected_allowed_signers_sha256=allowed_sha,
            now="2026-09-30T09:02:00Z",
        )

        assert (
            report[
                "human_repeat_paired_paper_entry_authorization_verified"
            ]
            is True
        )
        assert report["signature_verified"] is True
        assert report["trust_root_digest_matches"] is True
        assert report["approval_not_expired"] is True
        assert report["previous_pair_id"] == "pair-1"
        assert report["pair_id"] == "pair-2"
        assert report["paper_pair_entry_executed"] is False
        assert report["live_submit_authorized"] is False

        with pytest.raises(ValueError, match="expired"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=request_path,
                payload_path=payload_path,
                signature_path=signature_path,
                allowed_signers_path=allowed_path,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-30T09:06:00Z",
            )

        with pytest.raises(ValueError, match="trust-root digest mismatch"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=request_path,
                payload_path=payload_path,
                signature_path=signature_path,
                allowed_signers_path=allowed_path,
                expected_allowed_signers_sha256="9" * 64,
                now="2026-09-30T09:02:00Z",
            )
    finally:
        temp.cleanup()


def test_resealed_verification_rejects_economic_drift(monkeypatch):
    temp, root, request_path = _setup(monkeypatch)
    try:
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-30T09:00:00Z",
            ttl_seconds=300,
            approval_id="11111111-2222-4333-8444-555555555555",
        )
        payload_path = _write(root / "payload.json", payload)
        signature_path = _write(root / "signature", b"signature-bytes")
        allowed_path = _write(root / "allowed", b"allowed-signers")
        signature_sha = _sha(signature_path.read_bytes())
        allowed_sha = _sha(allowed_path.read_bytes())
        monkeypatch.setattr(
            MODULE,
            "_verify_signature",
            lambda **kwargs: (signature_sha, allowed_sha),
        )
        report = MODULE.verify_authorization(
            source_tree=ROOT,
            request_path=request_path,
            payload_path=payload_path,
            signature_path=signature_path,
            allowed_signers_path=allowed_path,
            expected_allowed_signers_sha256=allowed_sha,
            now="2026-09-30T09:02:00Z",
        )
    finally:
        temp.cleanup()

    report["required_pair_cash_quote"] = 2000.0
    _reseal_verification(report)
    with pytest.raises(ValueError, match="economic binding mismatch"):
        MODULE.validate_verification(report)


def test_repeat_signer_has_no_execution_or_promotion_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "open_paired_ml_paper_entries(" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "promote_continuous_challenger(" not in source
    assert "send_transaction" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_pair_entry_executed": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
