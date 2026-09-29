from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "run_phase8_offline_step_once.py"
)

SPEC = importlib.util.spec_from_file_location(
    "run_phase8_offline_step_once",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


DEBT_TYPE = "RETRAIN_OFFLINE_TRAIN_READY"
SCOPE = "cycle-1"
APPROVAL_ID = "11111111-2222-4333-8444-555555555555"


class _Record:
    def __init__(self, value: dict):
        self.value = value

    def to_record(self) -> dict:
        return copy.deepcopy(self.value)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _planner(
    *,
    debt_type: str | None,
    scope: str | None,
    persisted: bool = False,
) -> dict:
    action = None
    if debt_type is not None:
        action = {
            "priority": 20,
            "debt_type": debt_type,
            "scope": scope,
            "blocking": True,
            "operator_required": False,
            "shell_command": "pio phase8-retrain-train-run",
            "reason": "offline step ready",
        }
    return {
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "as_of": "2026-09-29T21:35:00Z",
        "promotion_ready": False,
        "persisted_phase8_current": persisted,
        "next_action": action,
        "items": [action] if action is not None else [],
        "reasons": [],
    }


def _readiness(production: Path, database: Path) -> dict:
    return {
        "readiness_sha256": "a" * 64,
        "fresh_phase8_handoff_sha256": "b" * 64,
        "offline_step_execution_readiness_ready": True,
        "requires_immediate_one_shot_offline_executor": True,
        "readiness_only": True,
        "debt_type": DEBT_TYPE,
        "scope": SCOPE,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": _sha(database.read_bytes()),
        "offline_step_request_sha256": "c" * 64,
        "fresh_signed_authorization_verification_sha256": "d" * 64,
        "approval_payload_sha256": "e" * 64,
        "approval_signature_sha256": "f" * 64,
        "allowed_signers_sha256": "1" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": APPROVAL_ID,
        "authorization_expires_at": "2026-09-29T21:40:00Z",
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    planner_before: dict | None = None,
    planner_after: dict | None = None,
    fresh_readiness_override: dict | None = None,
    operation_raises: bool = False,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"phase8-before")

    saved = _readiness(production, database)
    fresh = copy.deepcopy(saved)
    fresh["readiness_sha256"] = "2" * 64
    fresh["fresh_phase8_handoff_sha256"] = "3" * 64
    if fresh_readiness_override:
        fresh.update(fresh_readiness_override)

    saved_path = _write(root / "readiness.json", saved)
    for name in (
        "handoff.json",
        "phase7-post.json",
        "request.json",
        "verification.json",
        "payload.json",
    ):
        _write(root / name, {"fixture": True})
    signature = root / "signature"
    signature.write_bytes(b"sig")
    allowed = root / "allowed"
    allowed.write_bytes(b"allowed")

    before_plan = copy.deepcopy(
        planner_before
        if planner_before is not None
        else _planner(debt_type=DEBT_TYPE, scope=SCOPE)
    )
    after_plan = copy.deepcopy(
        planner_after
        if planner_after is not None
        else _planner(
            debt_type="RETRAIN_OFFLINE_VALIDATION_READY",
            scope=SCOPE,
        )
    )

    class FakeReadiness:
        @staticmethod
        def validate_phase8_offline_step_execution_readiness(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_offline_step_execution_readiness(**kwargs):
            return copy.deepcopy(fresh)

    class FakeStorage:
        def __init__(self, path):
            self.path = Path(path)

    plan_calls = {"count": 0}
    operation_calls = {"count": 0}

    def build_plan(storage):
        plan_calls["count"] += 1
        return _Record(
            before_plan if plan_calls["count"] == 1 else after_plan
        )

    def train(storage, *, cycle_id, artifact_directory):
        operation_calls["count"] += 1
        assert cycle_id == SCOPE
        expected_root = (
            storage.path.parent
            / "phase8_ml_artifacts"
            / SCOPE
        )
        assert Path(artifact_directory) == expected_root
        if operation_raises:
            raise RuntimeError("offline training failed")
        storage.path.write_bytes(b"phase8-after")
        expected_root.mkdir(parents=True)
        (expected_root / "model.json").write_text(
            '{"model":"challenger-1"}',
            encoding="utf-8",
        )
        return _Record(
            {
                "cycle_id": cycle_id,
                "model_id": "challenger-1",
                "research_only": True,
                "offline_only": True,
            }
        )

    runtime = {
        "Storage": FakeStorage,
        "build_phase8_evidence_plan": build_plan,
        "load_phase8_retrain_inputs": lambda *args, **kwargs: None,
        "run_phase8_retrain_build_from_inputs": (
            lambda *args, **kwargs: None
        ),
        "train_phase8_cycle_challenger": train,
        "validate_phase8_cycle_challenger_offline": (
            lambda *args, **kwargs: SimpleNamespace(
                offline_qualified=True,
                to_record=lambda: {},
            )
        ),
    }

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (FakeReadiness, runtime),
    )
    monkeypatch.setattr(
        MODULE,
        "LOCK_PATH",
        root / "phase8-one-shot.lock",
    )

    def run():
        return MODULE.execute_phase8_offline_step_once(
            repository=production,
            source_tree=ROOT,
            saved_readiness_path=saved_path,
            saved_phase8_handoff_path=root / "handoff.json",
            phase7_post_promotion_audit_path=root / "phase7-post.json",
            offline_step_request_path=root / "request.json",
            saved_signed_authorization_verification_path=(
                root / "verification.json"
            ),
            signed_payload_path=root / "payload.json",
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="1" * 64,
            now="2026-09-29T21:35:00Z",
        )

    return temp, database, operation_calls, run


def _reseal(receipt: dict) -> None:
    identity = {field: receipt[field] for field in MODULE.RECEIPT_FIELDS}
    receipt["receipt_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_one_shot_executes_exact_authorized_offline_step_once(
    monkeypatch,
):
    temp, _, calls, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    assert calls["count"] == 1
    assert receipt["debt_type"] == DEBT_TYPE
    assert receipt["scope"] == SCOPE
    assert receipt["operation_status"] == "COMPLETE"
    assert receipt["fresh_readiness_matches_saved"] is True
    assert receipt["exact_planner_action_matched"] is True
    assert receipt["offline_step_execution_authorized"] is True
    assert receipt["offline_step_attempted"] is True
    assert receipt["offline_step_executed"] is True
    assert receipt["offline_step_execution_completed"] is True
    assert receipt["step_progressed"] is True
    assert receipt["requires_post_step_audit"] is True
    assert receipt["production_pio_database_modified"] is True
    assert receipt["production_research_artifacts_modified"] is True
    assert receipt["research_artifact_change_count"] == 1
    assert receipt["research_artifact_changes"][0]["status"] == "ADDED"
    assert receipt["research_artifact_changes"][0]["path"] == (
        "phase8_ml_artifacts/cycle-1/model.json"
    )
    assert receipt["paper_challenger_transition_performed"] is False
    assert receipt["paper_trading_authorized"] is False
    assert receipt["live_submit_authorized"] is False
    assert receipt["transaction_submission_performed"] is False
    assert receipt["new_live_capital_used"] is False
    assert receipt["phase8_execution_authorized"] is False
    assert receipt["phase8_promotion_authorized"] is False


def test_fresh_readiness_allows_only_ephemeral_handoff_digest_drift(
    monkeypatch,
):
    temp, _, calls, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    assert calls["count"] == 1
    assert receipt["saved_readiness_sha256"] != receipt[
        "fresh_readiness_sha256"
    ]
    assert receipt["saved_readiness_stable_sha256"] == receipt[
        "fresh_readiness_stable_sha256"
    ]


def test_fresh_readiness_substantive_drift_fails_before_operation(
    monkeypatch,
):
    temp, _, calls, run = _build(
        monkeypatch,
        fresh_readiness_override={"scope": "cycle-2"},
    )
    try:
        with pytest.raises(ValueError, match="stable state differs"):
            run()
    finally:
        temp.cleanup()

    assert calls["count"] == 0


def test_planner_drift_fails_before_operation(monkeypatch):
    temp, _, calls, run = _build(
        monkeypatch,
        planner_before=_planner(
            debt_type="RETRAIN_OFFLINE_VALIDATION_READY",
            scope=SCOPE,
        ),
    )
    try:
        with pytest.raises(
            ValueError,
            match="differs from authorized exact step",
        ):
            run()
    finally:
        temp.cleanup()

    assert calls["count"] == 0


def test_operator_required_planner_action_fails_before_operation(
    monkeypatch,
):
    plan = _planner(debt_type=DEBT_TYPE, scope=SCOPE)
    plan["next_action"]["operator_required"] = True
    temp, _, calls, run = _build(
        monkeypatch,
        planner_before=plan,
    )
    try:
        with pytest.raises(ValueError, match="requires an operator"):
            run()
    finally:
        temp.cleanup()

    assert calls["count"] == 0


def test_failed_offline_operation_still_emits_auditable_receipt(
    monkeypatch,
):
    temp, _, calls, run = _build(
        monkeypatch,
        operation_raises=True,
        planner_after=_planner(debt_type=DEBT_TYPE, scope=SCOPE),
    )
    try:
        receipt = run()
    finally:
        temp.cleanup()

    assert calls["count"] == 1
    assert receipt["operation_status"] == "FAILED"
    assert receipt["operation"] is None
    assert "offline training failed" in receipt["error"]
    assert receipt["offline_step_attempted"] is True
    assert receipt["offline_step_executed"] is True
    assert receipt["offline_step_execution_completed"] is False
    assert receipt["step_progressed"] is False
    assert receipt["requires_post_step_audit"] is True


def test_resealed_receipt_cannot_claim_paper_transition(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["paper_challenger_transition_performed"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="paper_challenger_transition_performed=false",
    ):
        MODULE.validate_phase8_offline_step_execution_receipt(receipt)


def test_resealed_receipt_cannot_claim_transaction_submission(
    monkeypatch,
):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["transaction_submission_performed"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="transaction_submission_performed=false",
    ):
        MODULE.validate_phase8_offline_step_execution_receipt(receipt)


def test_resealed_receipt_cannot_authorize_phase8_execution(
    monkeypatch,
):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["phase8_execution_authorized"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="phase8_execution_authorized=false",
    ):
        MODULE.validate_phase8_offline_step_execution_receipt(receipt)


def test_executor_has_no_paper_or_live_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "start_model_paper_challenger(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "controlled-live-submit" not in source
    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert '"paper_challenger_transition_performed": False' in source
    assert '"transaction_submission_performed": False' in source
    assert '"new_live_capital_used": False' in source
    assert '"production_research_artifacts_modified": artifacts_modified' in source
    assert '"phase8_execution_authorized": False' in source
    assert '"phase8_promotion_authorized": False' in source
