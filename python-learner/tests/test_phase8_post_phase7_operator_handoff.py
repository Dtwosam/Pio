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
    / "build_phase8_post_phase7_operator_handoff.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_post_phase7_operator_handoff",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _FakePost:
    @staticmethod
    def validate_post_promotion_audit(value):
        assert isinstance(value, dict)


class _FakeStorage:
    def __init__(self, path):
        self.path = Path(path)


def _status() -> dict:
    return {
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "as_of": "2026-09-29T19:40:00+00:00",
        "phase7_promoted": True,
        "champion_model_id": "champion-1",
        "champion_dataset_version": "dataset-1",
        "champion_age_days": 1.0,
        "active_challenger_model_ids": [],
        "retrain_status": "WAITING_TRIGGER",
        "retrain_due": False,
        "new_chain_observations": 10,
        "required_new_chain_observations": 10,
        "new_chain_pools": 2,
        "required_new_chain_pools": 2,
        "new_live_labels_since_champion": 1,
        "retrain_live_label_trigger": 3,
        "champion_age_trigger_days": 7.0,
        "active_cycle_id": None,
        "active_cycle_status": None,
        "active_cycle_challenger_model_id": None,
        "active_cycle_challenger_status": None,
        "completed_cycles": 0,
        "champion_cycle_id": None,
        "continuous_promotion_evidence_id": None,
        "live_champion_status": "HEALTHY",
        "live_label_count": 1,
        "required_live_labels": 5,
        "live_distinct_pools": 1,
        "required_live_pools": 2,
        "live_rollback_recommended": False,
        "promotion_ready": False,
        "persisted_phase8_current": False,
        "reasons": ["more research evidence required"],
    }


def _operator() -> dict:
    return {
        "research_only": True,
        "read_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "as_of": "2026-09-29T19:40:01+00:00",
        "status": "WAITING_EVIDENCE",
        "promotion_ready": False,
        "persisted_phase8_current": False,
        "debt_type": "LIVE_LABEL_DEPTH_REQUIRED",
        "scope": "champion-1",
        "reason": "more live research labels required",
        "automatic_action_available": False,
        "operator_action_required": False,
        "manual_input_required": False,
        "suggested_command": None,
        "retrain_input_template": None,
        "required_manual_fields": [],
        "followup_commands": [],
        "operator_blockers": [],
        "reasons": ["more research evidence required"],
    }


def _post(database: Path) -> dict:
    return {
        "post_promotion_audit_sha256": "a" * 64,
        "pio_database_path": str(database),
        "pio_database_sha256_after": "b" * 64,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
        "post_promotion_audit_ready": True,
        "phase7_promotion_confirmed": True,
        "phase7_promotion_persisted": True,
        "requires_separate_phase8_workflow": True,
        "source_database_unchanged": True,
        "promotion_snapshot_read_only": True,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "production_pio_database_modified_by_audit": False,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    db_sha: str = "b" * 64,
    status_override: dict | None = None,
    operator_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        data = production / "data"
        data.mkdir(parents=True)
        database = data / "pio.db"
        database.write_bytes(b"production")
        post = _post(database)
        post_path = _write(root / "post.json", post)

        status = _status()
        operator = _operator()
        if status_override:
            status.update(status_override)
        if operator_override:
            operator.update(operator_override)

        class _Value:
            def __init__(self, value):
                self.value = value

            def to_record(self):
                return copy.deepcopy(self.value)

        runtime = {
            "Storage": _FakeStorage,
            "evaluate_phase8_evidence_status": (
                lambda storage: _Value(status)
            ),
            "build_phase8_operator_handoff": (
                lambda storage: _Value(operator)
            ),
        }

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakePost, runtime),
        )
        monkeypatch.setattr(
            MODULE,
            "_database_state",
            lambda path: {
                "database": db_sha,
                "wal": None,
                "shm": None,
            },
        )
        monkeypatch.setattr(
            MODULE,
            "_snapshot_sqlite",
            lambda source, destination: destination.write_bytes(b"copy"),
        )

        return MODULE.build_phase8_post_phase7_handoff(
            repository=production,
            source_tree=ROOT,
            phase7_post_promotion_audit_path=post_path,
            expected_phase7_post_promotion_audit_sha256=(
                post["post_promotion_audit_sha256"]
            ),
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["handoff_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_handoff_keeps_phase8_research_only(monkeypatch):
    report = _build(monkeypatch)

    assert report["phase7_promotion_confirmed"] is True
    assert report["phase7_promotion_persisted"] is True
    assert report["phase8_phase7_dependency_satisfied"] is True
    assert report["phase8_research_only"] is True
    assert report["phase8_read_only"] is True
    assert report["phase8_policy_actionable"] is False
    assert report["phase8_execution_wired"] is False
    assert report["phase8_status"] == "WAITING_EVIDENCE"
    assert report["phase8_handoff_ready"] is True
    assert report["requires_separate_phase8_action"] is True
    assert report["phase8_execution_authorized"] is False
    assert report["phase8_policy_action_authorized"] is False
    assert report["production_pio_database_modified"] is False


def test_database_must_match_phase7_post_promotion_audit(monkeypatch):
    with pytest.raises(
        ValueError,
        match="changed after Phase 7 post-promotion audit",
    ):
        _build(monkeypatch, db_sha="f" * 64)


def test_phase8_must_see_phase7_promoted(monkeypatch):
    with pytest.raises(
        ValueError,
        match="does not see Phase 7 promoted",
    ):
        _build(
            monkeypatch,
            status_override={"phase7_promoted": False},
        )


def test_phase8_policy_actionability_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="safety boundary changed",
    ):
        _build(
            monkeypatch,
            status_override={"policy_actionable": True},
        )


def test_phase8_execution_wiring_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="safety boundary changed",
    ):
        _build(
            monkeypatch,
            operator_override={"execution_wired": True},
        )


def test_resealed_handoff_cannot_authorize_phase8_execution(monkeypatch):
    report = _build(monkeypatch)
    report["phase8_execution_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase8_execution_authorized=false",
    ):
        MODULE.validate_phase8_post_phase7_handoff(report)


def test_resealed_handoff_cannot_authorize_live_submit(monkeypatch):
    report = _build(monkeypatch)
    report["live_submit_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="live_submit_authorized=false",
    ):
        MODULE.validate_phase8_post_phase7_handoff(report)


def test_phase8_handoff_is_private_snapshot_only():
    source = TOOL.read_text(encoding="utf-8")

    assert "mode=ro" in source
    assert "_snapshot_sqlite(database, private_db)" in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert '"phase8_policy_action_authorized": False' in source
    assert '"phase8_execution_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"production_pio_database_modified": False' in source
