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
    / "build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_signed_authorization.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_signed_authorization",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

APPROVAL_ID = "11111111-2222-4333-8444-555555555555"


def _request() -> dict:
    return {
        "request_sha256": "a" * 64,
        "source_settlement_readiness_sha256": "b" * 64,
        "source_terminal_evaluation_sha256": "c" * 64,
        "source_terminal_post_audit_sha256": "d" * 64,
        "source_recursive_rollover_reentry_cycle_post_audit_sha256": "e" * 64,
        "recursive_rollover_reentry_cycle_entry_request_sha256": "f" * 64,
        "recursive_rollover_reentry_cycle_input_verification_sha256": "1" * 64,
        "source_final_evaluation_sha256": "2" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "3" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-1",
        "pair_id": "pair-2",
        "pool_address": "pool-2",
        "entry_observed_at": "2026-09-30T09:20:00+00:00",
        "terminal_tick_observed_at": "2026-09-30T09:30:00+00:00",
        "incumbent_position_id": "p8-pair-2-incumbent",
        "challenger_position_id": "p8-pair-2-challenger",
        "open_position_id": "p8-pair-2-challenger",
        "closed_position_id": "p8-pair-2-incumbent",
        "open_position_policy_source": "ML_CHALLENGER",
        "open_position_model_id": "challenger-1",
        "requested_position_ids": ["p8-pair-2-challenger"],
        "settlement_cycle_id": "phase8-repeat-rollover-settle:pair-2:abc123",
        "target_chain_observed_at": "2026-09-30T09:35:00+00:00",
        "evaluation_as_of": "2026-09-30T09:36:00+00:00",
        "chain_max_age_seconds": 300,
        "quote_max_age_seconds": 300,
        "required_quote_mints": ["y-mint"],
        "fresh_quote_map": {"y-mint": 0.01},
        "quote_statuses_sha256": "4" * 64,
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
        "fresh_settlement_readiness_recheck_required": True,
        "exact_chain_snapshot_required": True,
        "exact_quote_map_required": True,
        "explicit_single_position_scope_required": True,
        "derived_pool_safety_required": True,
        "idempotent_single_position_run_required": True,
        "post_tick_audit_required": True,
        "paper_settlement_tick_authorization_present": False,
        "paper_settlement_tick_authorized": False,
        "paper_settlement_tick_executed": False,
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
    def validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request(value):
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
    payload = MODULE.build_payload(
        source_tree=ROOT,
        request_path=request_path,
        approver_principal="ops@example.com",
        issued_at="2026-09-30T09:40:00Z",
        ttl_seconds=300,
        approval_id=APPROVAL_ID,
    )
    return temp, root, request_path, payload


def _reseal(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.VERIFICATION_FIELDS
    }
    report["verification_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_settlement_request_is_exactly_pinned():
    path = ROOT / MODULE.REQUEST_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.REQUEST_TOOL
    ]


def test_payload_binds_exact_single_position_request(monkeypatch):
    temp, _, _, payload = _payload(monkeypatch)
    try:
        assert payload["open_position_id"] == "p8-pair-2-challenger"
        assert payload["closed_position_id"] == "p8-pair-2-incumbent"
        assert payload["requested_position_ids"] == ["p8-pair-2-challenger"]
        assert payload["previous_pair_id"] == "pair-1"
        assert payload["pair_id"] == "pair-2"
        assert payload["source_recursive_rollover_reentry_cycle_post_audit_sha256"] == "e" * 64
        assert payload["recursive_rollover_reentry_cycle_entry_request_sha256"] == "f" * 64
        assert payload["recursive_rollover_reentry_cycle_input_verification_sha256"] == "1" * 64
        assert payload["source_final_evaluation_sha256"] == "2" * 64
        assert payload["entry_observed_at"] == "2026-09-30T09:20:00+00:00"
        assert payload["target_chain_observed_at"] == (
            "2026-09-30T09:35:00+00:00"
        )
        assert payload["human_authorization_intent"] is True
        assert payload["explicit_single_position_scope_required"] is True
        assert payload["paper_settlement_tick_executed"] is False
        assert payload["paper_trading_authorized"] is False
        assert payload["live_submit_authorized"] is False
        assert payload["continuous_promotion_authorized"] is False
    finally:
        temp.cleanup()


def test_payload_rejects_cycle_pair_id_regression(monkeypatch):
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
                issued_at="2026-09-30T09:40:00Z",
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
                issued_at="2026-09-30T09:40:00Z",
                ttl_seconds=MODULE.MAX_TTL_SECONDS + 1,
                approval_id=APPROVAL_ID,
            )


def test_payload_rejects_position_scope_drift(monkeypatch):
    temp, _, _, payload = _payload(monkeypatch)
    try:
        request = _request()
        payload["requested_position_ids"] = [
            "p8-pair-2-incumbent",
            "p8-pair-2-challenger",
        ]
        identity = {
            field: payload[field]
            for field in MODULE.PAYLOAD_FIELDS
        }
        payload["payload_sha256"] = hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest()

        with pytest.raises(ValueError, match="position scope mismatch"):
            MODULE.validate_payload(payload, request=request)
    finally:
        temp.cleanup()


def test_verification_checks_signature_and_expiry(monkeypatch):
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
            issued_at="2026-09-30T09:40:00Z",
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
            now="2026-09-30T09:44:00Z",
        )
        assert report["human_recursive_rollover_reentry_cycle_settlement_tick_authorization_verified"] is True
        assert report["approval_not_expired"] is True
        assert report["requested_position_ids"] == ["p8-pair-2-challenger"]
        assert report["paper_settlement_tick_executed"] is False

        with pytest.raises(ValueError, match="expired"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                request_path=request_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-30T09:46:00Z",
            )


def test_wrong_trust_root_fails_closed(monkeypatch):
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
            issued_at="2026-09-30T09:40:00Z",
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
                now="2026-09-30T09:44:00Z",
            )


def _verified_report(monkeypatch) -> dict:
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
        issued_at="2026-09-30T09:40:00Z",
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
        now="2026-09-30T09:44:00Z",
    )
    temp.cleanup()
    return report


def test_resealed_verification_cannot_claim_execution(monkeypatch):
    report = _verified_report(monkeypatch)
    report["paper_settlement_tick_executed"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_settlement_tick_executed=false",
    ):
        MODULE.validate_verification(report)


def test_resealed_verification_cannot_authorize_broad_paper(monkeypatch):
    report = _verified_report(monkeypatch)
    report["paper_trading_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_verification(report)


def test_resealed_verification_cannot_authorize_promotion(monkeypatch):
    report = _verified_report(monkeypatch)
    report["continuous_promotion_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="continuous_promotion_authorized=false",
    ):
        MODULE.validate_verification(report)


def test_signer_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "apply_paper_chain_valuation(" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "send_transaction" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_settlement_tick_executed": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
