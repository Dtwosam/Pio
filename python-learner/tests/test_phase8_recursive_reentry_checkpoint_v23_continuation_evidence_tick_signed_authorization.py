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
    / "build_phase8_recursive_reentry_checkpoint_v23_continuation_evidence_tick_signed_authorization.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_recursive_reentry_checkpoint_v23_continuation_evidence_tick_signed_authorization",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

APPROVAL_ID = "11111111-2222-4333-8444-555555555555"


def _request() -> dict:
    return {
        "request_sha256": "a" * 64,
        "source_continuation_supervision_readiness_sha256": "b" * 64,
        "source_checkpoint_sha256": "c" * 64,
        "source_post_audit_sha256": "d" * 64,
        "source_execution_receipt_sha256": "e" * 64,
        "source_pair_entry_post_audit_sha256": "f" * 64,
        "pair_entry_request_sha256": "1" * 64,
        "pair_entry_input_verification_sha256": "4" * 64,
        "pair_lineage_sha256": "5" * 64,
        "latest_previous_tick_post_audit_sha256": "6" * 64,
        "source_final_evaluation_sha256": "7" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "2" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-3",
        "pair_id": "pair-4",
        "pool_address": "pool-4",
        "entry_observed_at": "2026-09-30T09:20:00+00:00",
        "previous_tick_observed_at": "2026-09-30T09:25:00+00:00",
        "incumbent_position_id": "p8-pair-4-incumbent",
        "challenger_position_id": "p8-pair-4-challenger",
        "requested_position_ids": [
            "p8-pair-4-incumbent",
            "p8-pair-4-challenger",
        ],
        "evidence_cycle_id": "phase8-recursive-reentry-checkpoint-continuation:pair-4:abc123",
        "target_chain_observed_at": "2026-09-30T09:30:00+00:00",
        "evaluation_as_of": "2026-09-30T09:31:00+00:00",
        "chain_max_age_seconds": 300,
        "quote_max_age_seconds": 300,
        "required_quote_mints": ["y-mint"],
        "fresh_quote_map": {"y-mint": 0.0001},
        "quote_statuses_sha256": "3" * 64,
        "pool_safety_config": {
            "min_tvl_usd": 50000.0,
            "min_volume_24h_usd": 10000.0,
            "min_pool_age_hours": 24.0,
            "min_chain_observations": 12,
            "max_dynamic_fee_pct": 5.0,
            "max_pool_snapshot_age_seconds": 900,
            "require_standard_spl": True,
            "require_not_blacklisted": True,
        },
        "position_management_config": {
            "stop_loss_bps": 500,
            "take_profit_bps": None,
            "max_rebalances": 3,
            "max_holding_observations": None,
            "proactive_rebalance_buffer_bins": 0,
        },
        "retry_failed": False,
        "emergency_exit": False,
        "estimated_exit_cost_quote": 0.0,
        "rebalance_cost_quote": None,
        "request_ready": True,
        "explicit_human_authorization_required": True,
        "fresh_continuation_readiness_recheck_required": True,
        "exact_chain_snapshot_required": True,
        "exact_quote_map_required": True,
        "explicit_position_scope_required": True,
        "derived_pool_safety_required": True,
        "idempotent_pair_run_required": True,
        "post_tick_audit_required": True,
        "paper_supervisor_tick_authorization_present": False,
        "paper_supervisor_tick_authorized": False,
        "paper_supervisor_tick_executed": False,
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
    def validate_phase8_recursive_reentry_checkpoint_v23_continuation_evidence_tick_request(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _payload(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_request",
        lambda source: _FakeRequest,
    )
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    request_path = _write(root / "request.json", _request())
    value = MODULE.build_payload(
        source_tree=ROOT,
        request_path=request_path,
        approver_principal="ops@example.com",
        issued_at="2026-09-30T09:32:00Z",
        ttl_seconds=300,
        approval_id=APPROVAL_ID,
    )
    return temp, root, request_path, value


def _reseal(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.VERIFICATION_FIELDS
    }
    report["verification_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_request_is_exactly_pinned():
    path = ROOT / MODULE.REQUEST_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.REQUEST_TOOL
    ]


def test_payload_binds_exact_tick_scope(monkeypatch):
    temp, _, _, payload = _payload(monkeypatch)
    try:
        assert payload["request_sha256"] == "a" * 64
        assert payload["source_checkpoint_sha256"] == "c" * 64
        assert payload["source_post_audit_sha256"] == "d" * 64
        assert payload["source_execution_receipt_sha256"] == "e" * 64
        assert payload["source_pair_entry_post_audit_sha256"] == "f" * 64
        assert payload["pair_entry_request_sha256"] == "1" * 64
        assert payload["pair_entry_input_verification_sha256"] == "4" * 64
        assert payload["pair_lineage_sha256"] == "5" * 64
        assert payload["latest_previous_tick_post_audit_sha256"] == "6" * 64
        assert payload["source_final_evaluation_sha256"] == "7" * 64
        assert payload["previous_pair_id"] == "pair-3"
        assert payload["pair_id"] == "pair-4"
        assert payload["previous_tick_observed_at"] == (
            "2026-09-30T09:25:00+00:00"
        )
        assert payload["requested_position_ids"] == [
            "p8-pair-4-incumbent",
            "p8-pair-4-challenger",
        ]
        assert payload["target_chain_observed_at"] == (
            "2026-09-30T09:30:00+00:00"
        )
        assert payload["fresh_quote_map"] == {"y-mint": 0.0001}
        assert payload["human_authorization_intent"] is True
        assert payload["fresh_continuation_readiness_recheck_required"] is True
        assert payload["exact_chain_snapshot_required"] is True
        assert payload["exact_quote_map_required"] is True
        assert payload["explicit_position_scope_required"] is True
        assert payload["derived_pool_safety_required"] is True
        assert payload["idempotent_pair_run_required"] is True
        assert payload["post_tick_audit_required"] is True
        assert payload["paper_supervisor_tick_executed"] is False
        assert payload["paper_evidence_collection_authorized"] is False
        assert payload["paper_trading_authorized"] is False
        assert payload["continuous_promotion_authorized"] is False
        assert payload["live_submit_authorized"] is False
    finally:
        temp.cleanup()


def test_payload_rejects_previous_tick_binding_drift(monkeypatch):
    temp, _, _, payload = _payload(monkeypatch)
    try:
        request = _request()
        payload["previous_tick_observed_at"] = (
            "2026-09-30T00:41:00+00:00"
        )
        identity = {
            field: payload[field]
            for field in MODULE.PAYLOAD_FIELDS
        }
        payload["payload_sha256"] = hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest()
        with pytest.raises(
            ValueError,
            match="previous_tick_observed_at binding mismatch",
        ):
            MODULE.validate_payload(payload, request=request)
    finally:
        temp.cleanup()


def test_checkpoint_payload_rejects_non_advanced_pair_id(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_request",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        request = _request()
        request["pair_id"] = request["previous_pair_id"]
        path = _write(Path(tmp) / "request.json", request)
        with pytest.raises(ValueError, match="pair id was not advanced"):
            MODULE.build_payload(
                source_tree=ROOT,
                request_path=path,
                approver_principal="ops@example.com",
                issued_at="2026-09-30T09:32:00Z",
                approval_id=APPROVAL_ID,
            )


def test_payload_rejects_excessive_ttl(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_request",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp) / "request.json", _request())
        with pytest.raises(ValueError, match="ttl_seconds"):
            MODULE.build_payload(
                source_tree=ROOT,
                request_path=path,
                approver_principal="ops@example.com",
                issued_at="2026-09-30T09:32:00Z",
                ttl_seconds=MODULE.MAX_TTL_SECONDS + 1,
                approval_id=APPROVAL_ID,
            )


def test_verification_checks_trust_root_and_expiry(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_request",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = _write(root / "request.json", _request())
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-30T09:32:00Z",
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
            now="2026-09-30T09:34:00Z",
        )
        assert report[
            "human_recursive_reentry_checkpoint_v23_continuation_pair_evidence_tick_authorization_verified"
        ] is True
        assert report["approval_not_expired"] is True
        assert report["paper_supervisor_tick_executed"] is False
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
                now="2026-09-30T09:38:00Z",
            )


def test_verification_rejects_wrong_trust_root(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_request",
        lambda source: _FakeRequest,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = _write(root / "request.json", _request())
        payload = MODULE.build_payload(
            source_tree=ROOT,
            request_path=request_path,
            approver_principal="ops@example.com",
            issued_at="2026-09-30T09:32:00Z",
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
                now="2026-09-30T09:34:00Z",
            )


def _verified(monkeypatch) -> dict:
    monkeypatch.setattr(
        MODULE,
        "_load_request",
        lambda source: _FakeRequest,
    )
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    request_path = _write(root / "request.json", _request())
    payload = MODULE.build_payload(
        source_tree=ROOT,
        request_path=request_path,
        approver_principal="ops@example.com",
        issued_at="2026-09-30T09:32:00Z",
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
    result = MODULE.verify_authorization(
        source_tree=ROOT,
        request_path=request_path,
        payload_path=payload_path,
        signature_path=signature,
        allowed_signers_path=allowed,
        expected_allowed_signers_sha256=allowed_sha,
        now="2026-09-30T09:34:00Z",
    )
    temp.cleanup()
    return result


def test_resealed_verification_cannot_claim_tick_executed(monkeypatch):
    report = _verified(monkeypatch)
    report["paper_supervisor_tick_executed"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_supervisor_tick_executed=false",
    ):
        MODULE.validate_verification(report)


def test_resealed_verification_cannot_authorize_broad_paper_trading(
    monkeypatch,
):
    report = _verified(monkeypatch)
    report["paper_trading_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_verification(report)


def test_resealed_verification_cannot_authorize_promotion(monkeypatch):
    temp, root, request_path, payload = _payload(monkeypatch)
    try:
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
            now="2026-09-30T09:34:00Z",
        )
    finally:
        temp.cleanup()

    report["continuous_promotion_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="continuous_promotion_authorized=false",
    ):
        MODULE.validate_verification(report)


def test_resealed_verification_cannot_authorize_live_submit(monkeypatch):
    report = _verified(monkeypatch)
    report["live_submit_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="live_submit_authorized=false",
    ):
        MODULE.validate_verification(report)


def test_signer_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_supervisor_tick_executed": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
