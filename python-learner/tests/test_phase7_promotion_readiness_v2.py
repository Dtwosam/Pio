from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "check_phase7_promotion_readiness_v2.py"

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_promotion_readiness_v2",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


DB_SHA = "d" * 64


def _handoff() -> dict:
    return {
        "handoff_sha256": "a" * 64,
        "saved_post_label_audit_sha256": "9" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "fresh_status_database_sha256_after": DB_SHA,
        "phase7_evidence_status_sha256": "b" * 64,
        "phase7_evidence_plan_sha256": "c" * 64,
        "continuation_route": "PHASE7_PROMOTION_REVIEW",
        "phase7_promotion_ready": True,
        "phase7_reasons": [],
        "confirmed_receipts": 6,
        "failed_receipts": 0,
        "closed_positions": 3,
        "open_positions": 0,
        "distinct_closed_pools": 2,
        "valued_closed_positions": 3,
        "labeled_closed_positions": 3,
        "ledger_clean": True,
    }


def _request() -> dict:
    return {
        "request_sha256": "1" * 64,
        "final_handoff_sha256": "a" * 64,
        "production_repository": "/opt/pio",
    }


def _verification() -> dict:
    return {
        "verification_sha256": "2" * 64,
        "request_sha256": "1" * 64,
        "human_phase7_promotion_authorization_verified": True,
        "approver_principal": "operator@example.com",
        "approval_id": "01234567-89ab-4def-8123-456789abcdef",
        "approval_payload_sha256": "3" * 64,
        "approval_signature_sha256": "4" * 64,
        "allowed_signers_sha256": "5" * 64,
    }


class _FakeHandoff:
    ROUTE_PROMOTION = "PHASE7_PROMOTION_REVIEW"
    fresh = None

    @staticmethod
    def validate_exit_final_evidence_handoff_v2(value):
        assert isinstance(value, dict)

    @classmethod
    def build_exit_final_evidence_handoff_v2(cls, **kwargs):
        return copy.deepcopy(cls.fresh)


class _FakeRequest:
    @staticmethod
    def validate_phase7_promotion_request_v2(value):
        assert isinstance(value, dict)


class _FakeSigner:
    fresh = None

    @staticmethod
    def validate_verification(value):
        assert isinstance(value, dict)

    @classmethod
    def verify_authorization(cls, **kwargs):
        return copy.deepcopy(cls.fresh)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    fresh_handoff_override=None,
    fresh_verification_override=None,
    anti_replay_override=None,
):
    handoff = _handoff()
    request = _request()
    verification = _verification()

    fresh_handoff = copy.deepcopy(handoff)
    if fresh_handoff_override:
        fresh_handoff.update(fresh_handoff_override)
    fresh_verification = copy.deepcopy(verification)
    if fresh_verification_override:
        fresh_verification.update(fresh_verification_override)

    _FakeHandoff.fresh = fresh_handoff
    _FakeSigner.fresh = fresh_verification

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (_FakeHandoff, _FakeRequest, _FakeSigner),
    )

    anti = {
        "phase6_dependency_current": True,
        "phase7_current_record_absent": True,
        "phase7_history_count": 0,
        "phase7_history_empty": True,
        "database_state": {
            "database": DB_SHA,
            "wal": None,
            "shm": None,
        },
    }
    if anti_replay_override:
        anti.update(anti_replay_override)
    monkeypatch.setattr(MODULE, "_promotion_snapshot", lambda database: anti)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        production.mkdir()
        database = production / "data" / "pio.db"
        database.parent.mkdir()
        database.write_bytes(b"db")

        handoff["production_repository"] = str(production)
        handoff["pio_database_path"] = str(database)
        fresh_handoff["production_repository"] = str(production)
        fresh_handoff["pio_database_path"] = str(database)
        request["production_repository"] = str(production)

        handoff_path = _write(root / "handoff.json", handoff)
        request_path = _write(root / "request.json", request)
        verification_path = _write(root / "verification.json", verification)
        post_label = _write(root / "post-label.json", {"unused": True})
        phase6 = _write(root / "phase6.json", {"unused": True})
        payload = _write(root / "payload.json", {"unused": True})
        signature = root / "sig"
        signature.write_bytes(b"sig")
        allowed = root / "allowed"
        allowed.write_bytes(b"allowed")

        return MODULE.build_phase7_promotion_readiness_v2(
            repository=production,
            source_tree=ROOT,
            saved_final_handoff_path=handoff_path,
            saved_post_label_audit_path=post_label,
            phase6_post_promotion_audit_path=phase6,
            promotion_request_path=request_path,
            saved_signed_authorization_verification_path=verification_path,
            signed_payload_path=payload,
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="5" * 64,
            now="2026-09-29T18:02:00Z",
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


def test_readiness_requires_fresh_exact_handoff_and_authorization(monkeypatch):
    report = _build(monkeypatch)

    assert report["final_handoff_matches_saved"] is True
    assert report["fresh_authorization_matches_saved"] is True
    assert report["human_phase7_promotion_authorization_verified"] is True
    assert report["approval_not_expired"] is True
    assert report["phase6_dependency_current"] is True
    assert report["phase7_current_record_absent"] is True
    assert report["phase7_history_empty"] is True
    assert report["anti_replay_ready"] is True
    assert report["requires_atomic_phase7_persistence"] is True
    assert report["phase7_promotion_persisted"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["new_live_capital_authorized"] is False


def test_fresh_handoff_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="differs from saved handoff"):
        _build(
            monkeypatch,
            fresh_handoff_override={"confirmed_receipts": 7},
        )


def test_fresh_authorization_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="authorization verification differs",
    ):
        _build(
            monkeypatch,
            fresh_verification_override={
                "verification_sha256": "f" * 64,
            },
        )


def test_phase6_dependency_must_remain_current(monkeypatch):
    with pytest.raises(
        ValueError,
        match="Phase 6 promotion dependency",
    ):
        _build(
            monkeypatch,
            anti_replay_override={"phase6_dependency_current": False},
        )


def test_existing_phase7_current_record_is_replay(monkeypatch):
    with pytest.raises(ValueError, match="already exists"):
        _build(
            monkeypatch,
            anti_replay_override={"phase7_current_record_absent": False},
        )


def test_existing_phase7_history_is_replay(monkeypatch):
    with pytest.raises(ValueError, match="history is not empty"):
        _build(
            monkeypatch,
            anti_replay_override={
                "phase7_history_count": 1,
                "phase7_history_empty": False,
            },
        )


def test_resealed_readiness_cannot_claim_persistence(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_persisted"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_persisted=false",
    ):
        MODULE.validate_phase7_promotion_readiness_v2(report)


def test_resealed_readiness_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_phase7_promotion_readiness_v2(report)


def test_readiness_is_read_only_and_non_authorizing():
    source = TOOL.read_text(encoding="utf-8")

    assert "mode=ro" in source
    assert "persist_phase7_promotion" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"phase7_promotion_persisted": False' in source
    assert '"transaction_submission_authorized": False' in source
