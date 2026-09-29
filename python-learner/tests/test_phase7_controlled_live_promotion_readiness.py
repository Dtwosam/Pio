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
    / "check_phase7_controlled_live_promotion_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_promotion_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _handoff(database: Path) -> dict:
    return {
        "handoff_sha256": "a" * 64,
        "saved_post_label_audit_sha256": "b" * 64,
        "continuation_route": "PHASE7_PROMOTION_REVIEW",
    }


def _request(database: Path) -> dict:
    return {
        "request_sha256": "c" * 64,
        "completion_handoff_sha256": "a" * 64,
        "phase7_evidence_status_sha256": "d" * 64,
        "phase7_evidence_plan_sha256": "e" * 64,
        "pio_database_path": str(database),
        "pio_database_sha256": "f" * 64,
        "confirmed_receipts": 6,
        "failed_receipts": 0,
        "closed_positions": 3,
        "open_positions": 0,
        "distinct_closed_pools": 2,
        "valued_closed_positions": 3,
        "labeled_closed_positions": 3,
    }


def _verification() -> dict:
    return {
        "verification_sha256": "1" * 64,
        "request_sha256": "c" * 64,
        "approval_payload_sha256": "2" * 64,
        "approval_signature_sha256": "3" * 64,
        "allowed_signers_sha256": "4" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "human_phase7_promotion_authorization_verified": True,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    fresh_handoff_override: dict | None = None,
    snapshot_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        data = production / "data"
        data.mkdir(parents=True)
        database = data / "pio.db"
        database.write_bytes(b"db")

        handoff = _handoff(database)
        request = _request(database)
        verification = _verification()
        fresh_handoff = copy.deepcopy(handoff)
        if fresh_handoff_override:
            fresh_handoff.update(fresh_handoff_override)

        class FakeHandoff:
            ROUTE_PROMOTION = "PHASE7_PROMOTION_REVIEW"

            @staticmethod
            def validate_exit_completion_evidence_handoff(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_exit_completion_evidence_handoff(**kwargs):
                return copy.deepcopy(fresh_handoff)

        class FakeRequest:
            @staticmethod
            def validate_phase7_promotion_request(value):
                assert isinstance(value, dict)

        class FakeSigner:
            @staticmethod
            def validate_verification(value):
                assert isinstance(value, dict)

            @staticmethod
            def verify_authorization(**kwargs):
                return copy.deepcopy(verification)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (FakeHandoff, FakeRequest, FakeSigner),
        )

        snapshot = {
            "phase6_dependency_current": True,
            "phase7_current_record_absent": True,
            "phase7_history_count": 0,
            "phase7_history_empty": True,
            "database_state": {
                "database": "f" * 64,
                "wal": None,
                "shm": None,
            },
        }
        if snapshot_override:
            snapshot.update(snapshot_override)
        monkeypatch.setattr(
            MODULE,
            "_promotion_snapshot",
            lambda database: copy.deepcopy(snapshot),
        )

        handoff_path = _write(root / "handoff.json", handoff)
        request_path = _write(root / "request.json", request)
        verification_path = _write(root / "verification.json", verification)
        post_path = _write(root / "post.json", {"unused": True})
        phase6_path = _write(root / "phase6.json", {"unused": True})
        payload_path = _write(root / "payload.json", {"unused": True})
        signature = root / "signature"
        signature.write_bytes(b"sig")
        allowed = root / "allowed"
        allowed.write_bytes(b"allowed")

        return MODULE.build_phase7_promotion_readiness(
            repository=production,
            source_tree=ROOT,
            saved_completion_handoff_path=handoff_path,
            saved_post_label_audit_path=post_path,
            phase6_post_promotion_audit_path=phase6_path,
            promotion_request_path=request_path,
            saved_signed_authorization_verification_path=verification_path,
            signed_payload_path=payload_path,
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="4" * 64,
            now="2026-09-29T18:00:00Z",
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


def test_ready_report_rechecks_authorization_and_antireplay(monkeypatch):
    report = _build(monkeypatch)

    assert report["fresh_handoff_matches_saved"] is True
    assert report["fresh_authorization_matches_saved"] is True
    assert report["human_phase7_promotion_authorization_verified"] is True
    assert report["phase6_dependency_current"] is True
    assert report["phase7_current_record_absent"] is True
    assert report["phase7_history_empty"] is True
    assert report["anti_replay_ready"] is True
    assert report["promotion_readiness_ready"] is True
    assert report["requires_atomic_phase7_persistence"] is True
    assert report["phase7_promotion_persisted"] is False
    assert report["live_submit_authorized"] is False
    assert report["new_live_capital_authorized"] is False


def test_fresh_handoff_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="fresh .* handoff differs"):
        _build(
            monkeypatch,
            fresh_handoff_override={"handoff_sha256": "9" * 64},
        )


def test_existing_phase7_current_record_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="current record already exists"):
        _build(
            monkeypatch,
            snapshot_override={"phase7_current_record_absent": False},
        )


def test_existing_phase7_history_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="history is not empty"):
        _build(
            monkeypatch,
            snapshot_override={
                "phase7_history_count": 1,
                "phase7_history_empty": False,
            },
        )


def test_phase6_dependency_must_still_be_current(monkeypatch):
    with pytest.raises(ValueError, match="Phase 6 promotion dependency"):
        _build(
            monkeypatch,
            snapshot_override={"phase6_dependency_current": False},
        )


def test_resealed_readiness_cannot_claim_persisted(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_persisted"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase7_promotion_persisted=false"):
        MODULE.validate_phase7_promotion_readiness(report)


def test_resealed_readiness_cannot_authorize_live_submit(monkeypatch):
    report = _build(monkeypatch)
    report["live_submit_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase7_promotion_readiness(report)


def test_readiness_has_no_persistence_or_transaction_execution():
    source = TOOL.read_text(encoding="utf-8")

    assert "persist_phase7_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"phase7_promotion_persisted": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
