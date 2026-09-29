from __future__ import annotations

import copy
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
    / "check_phase8_offline_step_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_offline_step_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _handoff(production: Path) -> dict:
    return {
        "handoff_sha256": "a" * 64,
        "saved_phase7_post_promotion_audit_sha256": "b" * 64,
        "production_repository": str(production),
        "pio_database_path": str(production / "data" / "pio.db"),
        "pio_database_sha256_after": "c" * 64,
        "phase8_operator_handoff": {
            "status": "AUTOMATIC_ACTION",
            "automatic_action_available": True,
            "operator_action_required": False,
            "manual_input_required": False,
            "debt_type": "RETRAIN_OFFLINE_TRAIN_READY",
            "scope": "cycle-1",
        },
    }


def _request(production: Path) -> dict:
    return {
        "request_sha256": "d" * 64,
        "saved_phase8_handoff_sha256": "a" * 64,
        "production_repository": str(production),
        "pio_database_path": str(production / "data" / "pio.db"),
        "pio_database_sha256": "c" * 64,
        "debt_type": "RETRAIN_OFFLINE_TRAIN_READY",
        "scope": "cycle-1",
    }


def _verification() -> dict:
    return {
        "verification_sha256": "e" * 64,
        "request_sha256": "d" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "human_offline_step_authorization_verified": True,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    fresh_handoff_override: dict | None = None,
    request_override: dict | None = None,
    fresh_verification_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        data = production / "data"
        data.mkdir(parents=True)
        (data / "pio.db").write_bytes(b"db")

        saved_handoff = _handoff(production)
        fresh_handoff = copy.deepcopy(saved_handoff)
        if fresh_handoff_override:
            for key, value in fresh_handoff_override.items():
                if key == "phase8_operator_handoff":
                    fresh_handoff[key].update(value)
                else:
                    fresh_handoff[key] = value

        request = _request(production)
        if request_override:
            request.update(request_override)

        saved_verification = _verification()
        fresh_verification = copy.deepcopy(saved_verification)
        if fresh_verification_override:
            fresh_verification.update(fresh_verification_override)

        class FakeHandoff:
            @staticmethod
            def validate_phase8_post_phase7_handoff(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_phase8_post_phase7_handoff(**kwargs):
                return copy.deepcopy(fresh_handoff)

        class FakeRequest:
            @staticmethod
            def validate_phase8_offline_step_request(value):
                assert isinstance(value, dict)

        class FakeSigner:
            @staticmethod
            def validate_verification(value):
                assert isinstance(value, dict)

            @staticmethod
            def verify_authorization(**kwargs):
                return copy.deepcopy(fresh_verification)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (FakeHandoff, FakeRequest, FakeSigner),
        )

        handoff_path = _write(root / "handoff.json", saved_handoff)
        request_path = _write(root / "request.json", request)
        verification_path = _write(
            root / "verification.json",
            saved_verification,
        )
        phase7_path = _write(root / "phase7.json", {"unused": True})
        payload_path = _write(root / "payload.json", {"unused": True})
        signature = root / "signature"
        signature.write_bytes(b"sig")
        allowed = root / "allowed"
        allowed.write_bytes(b"allowed")

        return MODULE.build_phase8_offline_step_readiness(
            repository=production,
            source_tree=ROOT,
            saved_phase8_handoff_path=handoff_path,
            phase7_post_promotion_audit_path=phase7_path,
            request_path=request_path,
            saved_signed_authorization_verification_path=verification_path,
            signed_payload_path=payload_path,
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="f" * 64,
            now="2026-09-29T20:00:00Z",
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_report_requires_fresh_exact_bindings(monkeypatch):
    report = _build(monkeypatch)

    assert report["fresh_handoff_matches_saved"] is True
    assert report["fresh_authorization_matches_saved"] is True
    assert report["human_offline_step_authorization_verified"] is True
    assert report["approval_not_expired"] is True
    assert report["current_action_still_automatic"] is True
    assert report["current_debt_type_matches_request"] is True
    assert report["current_scope_matches_request"] is True
    assert report["database_binding_current"] is True
    assert report["offline_step_readiness_ready"] is True
    assert report["requires_separate_offline_step_executor"] is True
    assert report["offline_step_executed"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_fresh_handoff_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="fresh Phase 8 handoff differs"):
        _build(
            monkeypatch,
            fresh_handoff_override={"handoff_sha256": "9" * 64},
        )


def test_action_no_longer_automatic_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="no longer automatic"):
        _build(
            monkeypatch,
            fresh_handoff_override={
                "phase8_operator_handoff": {
                    "status": "MANUAL_REQUIRED",
                    "automatic_action_available": False,
                    "operator_action_required": True,
                }
            },
        )


def test_fresh_debt_type_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="debt type differs"):
        _build(
            monkeypatch,
            fresh_handoff_override={
                "phase8_operator_handoff": {
                    "debt_type": "RETRAIN_OFFLINE_VALIDATION_READY",
                }
            },
        )


def test_fresh_scope_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="scope differs"):
        _build(
            monkeypatch,
            fresh_handoff_override={
                "phase8_operator_handoff": {"scope": "cycle-2"},
            },
        )


def test_fresh_database_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="database differs"):
        _build(
            monkeypatch,
            fresh_handoff_override={
                "pio_database_sha256_after": "9" * 64,
            },
            request_override={
                "saved_phase8_handoff_sha256": "a" * 64,
            },
        )


def test_fresh_signed_verification_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="signed authorization differs",
    ):
        _build(
            monkeypatch,
            fresh_verification_override={
                "verification_sha256": "9" * 64,
            },
        )


def test_resealed_readiness_cannot_authorize_paper_transition(monkeypatch):
    report = _build(monkeypatch)
    report["paper_challenger_transition_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="paper_challenger_transition_authorized=false",
    ):
        MODULE.validate_phase8_offline_step_readiness(report)


def test_resealed_readiness_cannot_authorize_live_submit(monkeypatch):
    report = _build(monkeypatch)
    report["live_submit_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="live_submit_authorized=false",
    ):
        MODULE.validate_phase8_offline_step_readiness(report)


def test_readiness_has_no_mutation_or_transaction_execution():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_phase8_evidence_step(" not in source
    assert "start_paper_challenger(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"offline_step_executed": False' in source
    assert '"paper_challenger_transition_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
