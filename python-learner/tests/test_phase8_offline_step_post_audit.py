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
    / "check_phase8_offline_step_post_audit.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_offline_step_post_audit",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


DEBT_TYPE = "RETRAIN_OFFLINE_TRAIN_READY"
SCOPE = "cycle-1"


class _Record:
    def __init__(self, value: dict):
        self.value = value

    def to_record(self) -> dict:
        return copy.deepcopy(self.value)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _action(
    debt_type: str,
    scope: str = SCOPE,
    *,
    operator_required: bool = False,
) -> dict:
    return {
        "priority": 25,
        "debt_type": debt_type,
        "scope": scope,
        "blocking": True,
        "operator_required": operator_required,
        "shell_command": "pio next-command",
        "reason": "next evidence step",
    }


def _plan(
    *,
    debt_type: str | None = "RETRAIN_OFFLINE_VALIDATION_READY",
    scope: str | None = SCOPE,
    persisted: bool = False,
    promotion_ready: bool = False,
    as_of: str = "2026-09-29T21:45:00Z",
) -> dict:
    next_action = (
        _action(debt_type, str(scope))
        if debt_type is not None and scope is not None
        else None
    )
    return {
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "as_of": as_of,
        "promotion_ready": promotion_ready,
        "persisted_phase8_current": persisted,
        "next_action": next_action,
        "items": [next_action] if next_action is not None else [],
        "reasons": [],
    }


def _status() -> dict:
    return {
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "as_of": "2026-09-29T21:45:00Z",
        "phase7_promoted": True,
        "promotion_ready": False,
        "persisted_phase8_current": False,
    }


def _handoff(
    plan: dict,
    *,
    status: str = "AUTOMATIC_ACTION",
) -> dict:
    action = plan.get("next_action")
    return {
        "research_only": True,
        "read_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "as_of": "2026-09-29T21:45:00Z",
        "status": status,
        "promotion_ready": bool(plan.get("promotion_ready")),
        "persisted_phase8_current": bool(
            plan.get("persisted_phase8_current")
        ),
        "debt_type": (
            action.get("debt_type")
            if isinstance(action, dict)
            else None
        ),
        "scope": (
            action.get("scope")
            if isinstance(action, dict)
            else None
        ),
    }


def _receipt(
    production: Path,
    database: Path,
    planner_after: dict,
    *,
    operation_status: str = "COMPLETE",
    completed: bool = True,
    progressed: bool = True,
) -> dict:
    return {
        "receipt_sha256": "a" * 64,
        "offline_step_request_sha256": "b" * 64,
        "signed_authorization_verification_sha256": "c" * 64,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_after": _sha(database.read_bytes()),
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
        "research_artifacts_after": [],
        "research_artifact_change_count": 0,
        "debt_type": DEBT_TYPE,
        "scope": SCOPE,
        "operation_status": operation_status,
        "offline_step_execution_completed": completed,
        "step_progressed": progressed,
        "planner_after": planner_after,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    receipt_status: str = "COMPLETE",
    receipt_completed: bool = True,
    receipt_progressed: bool = True,
    receipt_plan: dict | None = None,
    fresh_plan: dict | None = None,
    fresh_status: dict | None = None,
    fresh_handoff: dict | None = None,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"phase8-post-step")

    receipt_plan_value = copy.deepcopy(
        receipt_plan
        if receipt_plan is not None
        else _plan(as_of="2026-09-29T21:44:59Z")
    )
    fresh_plan_value = copy.deepcopy(
        fresh_plan
        if fresh_plan is not None
        else _plan(as_of="2026-09-29T21:45:01Z")
    )
    status_value = copy.deepcopy(
        fresh_status if fresh_status is not None else _status()
    )
    handoff_value = copy.deepcopy(
        fresh_handoff
        if fresh_handoff is not None
        else _handoff(fresh_plan_value)
    )
    receipt = _receipt(
        production,
        database,
        receipt_plan_value,
        operation_status=receipt_status,
        completed=receipt_completed,
        progressed=receipt_progressed,
    )
    receipt_path = _write(root / "receipt.json", receipt)

    class FakeExecutor:
        @staticmethod
        def validate_phase8_offline_step_execution_receipt(value):
            assert isinstance(value, dict)

        @staticmethod
        def _research_artifacts(data_root):
            return []

    class FakeStorage:
        def __init__(self, path):
            self.path = Path(path)

    runtime = {
        "Storage": FakeStorage,
        "evaluate_phase8_evidence_status": (
            lambda storage: _Record(status_value)
        ),
        "build_phase8_evidence_plan": (
            lambda storage: _Record(fresh_plan_value)
        ),
        "build_phase8_operator_handoff": (
            lambda storage: _Record(handoff_value)
        ),
    }

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (FakeExecutor, runtime),
    )
    monkeypatch.setattr(
        MODULE,
        "_snapshot_sqlite",
        lambda source, destination: destination.write_bytes(
            source.read_bytes()
        ),
    )

    def run():
        return MODULE.build_phase8_offline_step_post_audit(
            repository=production,
            source_tree=ROOT,
            execution_receipt_path=receipt_path,
        )

    return temp, run


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["post_audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_successful_progress_routes_next_offline_step_to_reauthorization(
    monkeypatch,
):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["production_database_matches_receipt"] is True
    assert report["production_database_unchanged_by_audit"] is True
    assert report["production_research_artifacts_match_receipt"] is True
    assert (
        report["production_research_artifacts_unchanged_by_audit"]
        is True
    )
    assert report["execution_research_artifact_change_count"] == 0
    assert report["receipt_planner_after_matches_fresh"] is True
    assert report["continuation_route"] == MODULE.ROUTE_NEXT_OFFLINE
    assert report["next_offline_step_reauthorization_required"] is True
    assert report["requires_new_human_authorization"] is True
    assert report["paper_challenger_review_required"] is False
    assert report["post_step_audit_ready"] is True
    assert report["paper_challenger_transition_authorized"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["phase8_execution_authorized"] is False


def test_offline_qualified_state_routes_to_paper_review_not_transition(
    monkeypatch,
):
    plan = _plan(
        debt_type="PAPER_CHALLENGER_START_REQUIRED",
        scope="challenger-1",
    )
    temp, run = _build(
        monkeypatch,
        receipt_plan=copy.deepcopy(plan),
        fresh_plan=copy.deepcopy(plan),
        fresh_handoff=_handoff(plan, status="MANUAL_REQUIRED"),
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["continuation_route"] == MODULE.ROUTE_PAPER_REVIEW
    assert report["paper_challenger_review_required"] is True
    assert report["requires_new_human_authorization"] is True
    assert report["paper_challenger_transition_authorized"] is False
    assert report["paper_trading_authorized"] is False


def test_phase8_promotion_ready_routes_to_separate_promotion_review(
    monkeypatch,
):
    plan = _plan(
        debt_type="PHASE8_PROMOTION_PERSISTENCE_REQUIRED",
        scope="PHASE8_PROMOTION",
        promotion_ready=True,
    )
    temp, run = _build(
        monkeypatch,
        receipt_plan=copy.deepcopy(plan),
        fresh_plan=copy.deepcopy(plan),
        fresh_handoff=_handoff(plan, status="MANUAL_REQUIRED"),
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["continuation_route"] == MODULE.ROUTE_PROMOTION_REVIEW
    assert report["phase8_promotion_review_required"] is True
    assert report["requires_new_human_authorization"] is True
    assert report["phase8_promotion_authorized"] is False


def test_failed_step_routes_to_failure_review_not_retry(monkeypatch):
    temp, run = _build(
        monkeypatch,
        receipt_status="FAILED",
        receipt_completed=False,
        receipt_progressed=False,
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["continuation_route"] == MODULE.ROUTE_FAILURE_REVIEW
    assert report["failure_review_required"] is True
    assert report["next_offline_step_reauthorization_required"] is False
    assert report["requires_new_human_authorization"] is False


def test_not_qualified_routes_to_review_not_retry(monkeypatch):
    temp, run = _build(
        monkeypatch,
        receipt_status="NOT_QUALIFIED",
        receipt_completed=True,
        receipt_progressed=False,
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert (
        report["continuation_route"]
        == MODULE.ROUTE_NOT_QUALIFIED_REVIEW
    )
    assert report["failure_review_required"] is True
    assert report["next_offline_step_reauthorization_required"] is False


def test_complete_without_progress_routes_to_review(monkeypatch):
    temp, run = _build(
        monkeypatch,
        receipt_status="COMPLETE",
        receipt_completed=True,
        receipt_progressed=False,
    )
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["continuation_route"] == MODULE.ROUTE_NO_PROGRESS_REVIEW
    assert report["failure_review_required"] is True
    assert report["next_offline_step_reauthorization_required"] is False


def test_fresh_planner_substantive_drift_fails_closed(monkeypatch):
    receipt_plan = _plan(
        debt_type="RETRAIN_OFFLINE_VALIDATION_READY",
        scope=SCOPE,
    )
    fresh_plan = _plan(
        debt_type="PAPER_CHALLENGER_START_REQUIRED",
        scope="challenger-1",
    )
    temp, run = _build(
        monkeypatch,
        receipt_plan=receipt_plan,
        fresh_plan=fresh_plan,
    )
    try:
        with pytest.raises(ValueError, match="planner differs"):
            run()
    finally:
        temp.cleanup()


def test_database_drift_after_execution_receipt_fails_closed(
    monkeypatch,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"before")
    plan = _plan()
    receipt = _receipt(production, database, plan)
    path = _write(root / "receipt.json", receipt)
    database.write_bytes(b"after-drift")

    class FakeExecutor:
        @staticmethod
        def validate_phase8_offline_step_execution_receipt(value):
            assert isinstance(value, dict)

        @staticmethod
        def _research_artifacts(data_root):
            return []

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (FakeExecutor, {}),
    )
    try:
        with pytest.raises(ValueError, match="changed after"):
            MODULE.build_phase8_offline_step_post_audit(
                repository=production,
                source_tree=ROOT,
                execution_receipt_path=path,
            )
    finally:
        temp.cleanup()


def test_research_artifact_drift_after_receipt_fails_closed(
    monkeypatch,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"stable")
    receipt = _receipt(production, database, _plan())
    path = _write(root / "receipt.json", receipt)

    class FakeExecutor:
        @staticmethod
        def validate_phase8_offline_step_execution_receipt(value):
            assert isinstance(value, dict)

        @staticmethod
        def _research_artifacts(data_root):
            return [
                {
                    "path": "phase8_ml_artifacts/cycle-1/model.json",
                    "size_bytes": 1,
                    "sha256": "a" * 64,
                }
            ]

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (FakeExecutor, {}),
    )
    try:
        with pytest.raises(ValueError, match="artifacts changed after"):
            MODULE.build_phase8_offline_step_post_audit(
                repository=production,
                source_tree=ROOT,
                execution_receipt_path=path,
            )
    finally:
        temp.cleanup()


def test_resealed_audit_cannot_authorize_paper_transition(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_challenger_transition_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_challenger_transition_authorized=false",
    ):
        MODULE.validate_phase8_offline_step_post_audit(report)


def test_resealed_audit_cannot_authorize_live_submit(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["live_submit_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="live_submit_authorized=false",
    ):
        MODULE.validate_phase8_offline_step_post_audit(report)


def test_post_step_audit_has_no_retry_paper_or_live_mutation():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_phase8_evidence_step(" not in source
    assert "train_phase8_cycle_challenger(" not in source
    assert "start_model_paper_challenger(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_challenger_transition_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
    assert '"production_pio_database_modified_by_audit": False' in source
