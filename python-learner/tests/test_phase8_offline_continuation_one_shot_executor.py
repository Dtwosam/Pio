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
    / "run_phase8_offline_continuation_step_once.py"
)

SPEC = importlib.util.spec_from_file_location(
    "run_phase8_offline_continuation_step_once",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


DEBT_TYPE = "RETRAIN_OFFLINE_VALIDATION_READY"
SCOPE = "cycle-1"


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
            "priority": 30,
            "debt_type": debt_type,
            "scope": scope,
            "blocking": True,
            "operator_required": False,
            "shell_command": "pio phase8-offline-step",
            "reason": "offline continuation ready",
        }
    return {
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "as_of": "2026-09-29T21:30:00Z",
        "promotion_ready": False,
        "persisted_phase8_current": persisted,
        "next_action": action,
        "items": [action] if action is not None else [],
        "reasons": [],
    }


def _readiness(production: Path, database: Path) -> dict:
    artifacts = []
    return {
        "readiness_sha256": "a" * 64,
        "fresh_post_audit_sha256": "b" * 64,
        "continuation_execution_readiness_ready": True,
        "requires_immediate_one_shot_continuation_executor": True,
        "readiness_only": True,
        "debt_type": DEBT_TYPE,
        "scope": SCOPE,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": _sha(database.read_bytes()),
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "research_artifacts_sha256": _sha(
            MODULE._canonical_bytes(artifacts)
        ),
        "research_artifact_count": 0,
        "saved_post_audit_sha256": "c" * 64,
        "source_execution_receipt_sha256": "d" * 64,
        "source_offline_step_request_sha256": "e" * 64,
        "continuation_request_sha256": "f" * 64,
        "fresh_signed_authorization_verification_sha256": "1" * 64,
        "approval_payload_sha256": "2" * 64,
        "approval_signature_sha256": "3" * 64,
        "allowed_signers_sha256": "4" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "authorization_expires_at": "2026-09-29T21:35:00Z",
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _artifact_inventory(data_root: Path) -> list[dict]:
    root = data_root / "phase8_ml_artifacts"
    if not root.exists():
        return []
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            payload = path.read_bytes()
            result.append(
                {
                    "path": path.relative_to(data_root).as_posix(),
                    "size_bytes": len(payload),
                    "sha256": _sha(payload),
                }
            )
    return result


def _artifact_changes(before, after):
    old = {x["path"]: x for x in before}
    new = {x["path"]: x for x in after}
    changes = []
    for path in sorted(set(old) | set(new)):
        a = old.get(path)
        b = new.get(path)
        if a == b:
            continue
        changes.append(
            {
                "path": path,
                "status": (
                    "ADDED"
                    if a is None
                    else "REMOVED"
                    if b is None
                    else "MODIFIED"
                ),
                "before_size_bytes": (
                    a["size_bytes"] if a is not None else None
                ),
                "after_size_bytes": (
                    b["size_bytes"] if b is not None else None
                ),
                "before_sha256": (
                    a["sha256"] if a is not None else None
                ),
                "after_sha256": (
                    b["sha256"] if b is not None else None
                ),
            }
        )
    return changes


def _build(
    monkeypatch,
    *,
    planner_before: dict | None = None,
    planner_after: dict | None = None,
    fresh_readiness_override: dict | None = None,
    operation_status: str = "COMPLETE",
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"phase8-continuation-before")

    saved = _readiness(production, database)
    fresh = copy.deepcopy(saved)
    fresh["readiness_sha256"] = "9" * 64
    fresh["fresh_post_audit_sha256"] = "8" * 64
    if fresh_readiness_override:
        fresh.update(fresh_readiness_override)

    saved_path = _write(root / "readiness.json", saved)
    for name in (
        "audit.json",
        "prior-receipt.json",
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
            debt_type="PAPER_CHALLENGER_START_REQUIRED",
            scope="challenger-1",
        )
    )
    plan_calls = {"count": 0}
    operation_calls = {"count": 0}

    class FakeReadiness:
        @staticmethod
        def validate_phase8_offline_continuation_execution_readiness(
            value,
        ):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_offline_continuation_execution_readiness(
            **kwargs,
        ):
            return copy.deepcopy(fresh)

    class FakeStorage:
        def __init__(self, path):
            self.path = Path(path)

    def build_plan(storage):
        plan_calls["count"] += 1
        return _Record(
            before_plan if plan_calls["count"] == 1 else after_plan
        )

    runtime = {
        "Storage": FakeStorage,
        "build_phase8_evidence_plan": build_plan,
    }

    class FakeOriginalExecutor:
        ALLOWED_ARTIFACT_ROOTS = (
            "phase8_retraining_datasets",
            "phase8_ml_artifacts",
        )

        @staticmethod
        def _production_database(production_path):
            return (Path(production_path) / "data" / "pio.db").resolve()

        @staticmethod
        def _database_state(database_path):
            database_path = Path(database_path)
            return {
                "database": _sha(database_path.read_bytes()),
                "wal": None,
                "shm": None,
            }

        @staticmethod
        def _research_artifacts(data_root):
            return _artifact_inventory(Path(data_root))

        @staticmethod
        def _artifact_changes(before, after):
            return _artifact_changes(before, after)

        @staticmethod
        def _record(value):
            return value.to_record()

        @staticmethod
        def _planner_progressed(before, after):
            old = before.get("next_action")
            new = after.get("next_action")
            if old is None:
                return False
            if new is None:
                return True
            return (
                old.get("debt_type"),
                old.get("scope"),
            ) != (
                new.get("debt_type"),
                new.get("scope"),
            )

        @staticmethod
        def _execute_exact_offline_step(
            runtime_value,
            storage,
            *,
            debt_type,
            scope,
            data_root,
        ):
            operation_calls["count"] += 1
            assert debt_type == DEBT_TYPE
            assert scope == SCOPE
            if operation_status == "FAILED":
                return "FAILED", None, "RuntimeError: offline failure"
            storage.path.write_bytes(b"phase8-continuation-after")
            artifact = (
                Path(data_root)
                / "phase8_ml_artifacts"
                / SCOPE
                / "validation.json"
            )
            artifact.parent.mkdir(parents=True)
            artifact.write_text('{"qualified":true}', encoding="utf-8")
            result = {
                "cycle_id": scope,
                "offline_qualified": (
                    operation_status == "COMPLETE"
                ),
                "research_only": True,
                "offline_only": True,
            }
            return operation_status, result, None

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (
            FakeReadiness,
            FakeOriginalExecutor,
            runtime,
        ),
    )
    monkeypatch.setattr(
        MODULE,
        "LOCK_PATH",
        root / "phase8-one-shot.lock",
    )

    def run():
        return MODULE.execute_phase8_offline_continuation_step_once(
            repository=production,
            source_tree=ROOT,
            saved_readiness_path=saved_path,
            saved_post_audit_path=root / "audit.json",
            execution_receipt_path=root / "prior-receipt.json",
            continuation_request_path=root / "request.json",
            saved_signed_authorization_verification_path=(
                root / "verification.json"
            ),
            signed_payload_path=root / "payload.json",
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="4" * 64,
            now="2026-09-29T21:30:00Z",
        )

    return temp, operation_calls, run, FakeOriginalExecutor


def _reseal(receipt: dict) -> None:
    identity = {
        field: receipt[field]
        for field in MODULE.RECEIPT_FIELDS
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_continuation_executor_runs_exact_step_once(monkeypatch):
    temp, calls, run, _ = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    assert calls["count"] == 1
    assert receipt["debt_type"] == DEBT_TYPE
    assert receipt["scope"] == SCOPE
    assert receipt["operation_status"] == "COMPLETE"
    assert receipt["fresh_readiness_matches_saved"] is True
    assert receipt[
        "human_continuation_offline_step_authorization_verified"
    ] is True
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
    assert receipt["paper_challenger_transition_performed"] is False
    assert receipt["paper_trading_authorized"] is False
    assert receipt["live_submit_authorized"] is False
    assert receipt["new_live_capital_used"] is False
    assert receipt["phase8_execution_authorized"] is False


def test_ephemeral_fresh_post_audit_digest_drift_is_allowed(
    monkeypatch,
):
    temp, calls, run, _ = _build(monkeypatch)
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


def test_substantive_readiness_drift_fails_before_operation(
    monkeypatch,
):
    temp, calls, run, _ = _build(
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
    temp, calls, run, _ = _build(
        monkeypatch,
        planner_before=_planner(
            debt_type="RETRAIN_OFFLINE_TRAIN_READY",
            scope=SCOPE,
        ),
    )
    try:
        with pytest.raises(ValueError, match="authorized step"):
            run()
    finally:
        temp.cleanup()

    assert calls["count"] == 0


def test_failed_operation_still_emits_auditable_receipt(
    monkeypatch,
):
    temp, calls, run, _ = _build(
        monkeypatch,
        operation_status="FAILED",
        planner_after=_planner(debt_type=DEBT_TYPE, scope=SCOPE),
    )
    try:
        receipt = run()
    finally:
        temp.cleanup()

    assert calls["count"] == 1
    assert receipt["operation_status"] == "FAILED"
    assert receipt["operation"] is None
    assert "offline failure" in receipt["error"]
    assert receipt["offline_step_attempted"] is True
    assert receipt["offline_step_executed"] is True
    assert receipt["offline_step_execution_completed"] is False
    assert receipt["step_progressed"] is False
    assert receipt["requires_post_step_audit"] is True


def test_resealed_receipt_cannot_claim_paper_transition(monkeypatch):
    temp, _, run, original = _build(monkeypatch)
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
        MODULE.validate_phase8_offline_continuation_execution_receipt(
            receipt,
            original_executor=original,
        )


def test_resealed_receipt_cannot_claim_transaction_submission(
    monkeypatch,
):
    temp, _, run, original = _build(monkeypatch)
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
        MODULE.validate_phase8_offline_continuation_execution_receipt(
            receipt,
            original_executor=original,
        )


def test_executor_uses_same_global_phase8_one_shot_lock():
    assert MODULE.LOCK_PATH == Path(
        "/var/tmp/pio-phase8-offline-step-one-shot.lock"
    )


def test_continuation_executor_has_no_paper_or_live_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "start_model_paper_challenger(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "controlled-live-submit" not in source
    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert '"paper_challenger_transition_performed": False' in source
    assert '"transaction_submission_performed": False' in source
    assert '"new_live_capital_used": False' in source
    assert '"phase8_execution_authorized": False' in source
    assert '"phase8_promotion_authorized": False' in source
