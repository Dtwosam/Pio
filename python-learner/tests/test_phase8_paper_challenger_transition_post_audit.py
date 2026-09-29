from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase8_paper_challenger_transition_post_audit.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paper_challenger_transition_post_audit",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

MODEL_ID = "challenger-1"
CYCLE_ID = "cycle-1"


class _Record:
    def __init__(self, value: dict):
        self.value = value

    def to_record(self) -> dict:
        return copy.deepcopy(self.value)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _status(
    *,
    cycle_status: str = "PAPER_CHALLENGER",
    model_status: str = "PAPER_CHALLENGER",
    model_id: str = MODEL_ID,
) -> dict:
    return {
        "as_of": "2026-09-29T22:10:00Z",
        "phase7_promoted": True,
        "active_cycle_id": CYCLE_ID,
        "active_cycle_status": cycle_status,
        "active_cycle_challenger_model_id": model_id,
        "active_cycle_challenger_status": model_status,
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
    }


def _plan(
    *,
    debt_type: str = "PAPER_CHALLENGER_EVIDENCE_REQUIRED",
    scope: str = MODEL_ID,
) -> dict:
    return {
        "as_of": "2026-09-29T22:10:00Z",
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "promotion_ready": False,
        "persisted_phase8_current": False,
        "next_action": {
            "priority": 35,
            "debt_type": debt_type,
            "scope": scope,
            "blocking": True,
            "operator_required": True,
            "shell_command": None,
            "reason": "PAPER evidence is required",
        },
        "items": [],
        "reasons": [],
    }


def _handoff(
    *,
    automatic: bool = False,
    status: str = "MANUAL_REQUIRED",
) -> dict:
    return {
        "as_of": "2026-09-29T22:10:00Z",
        "research_only": True,
        "read_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "status": status,
        "promotion_ready": False,
        "persisted_phase8_current": False,
        "debt_type": "PAPER_CHALLENGER_EVIDENCE_REQUIRED",
        "scope": MODEL_ID,
        "reason": "PAPER evidence is required",
        "automatic_action_available": automatic,
        "operator_action_required": True,
        "manual_input_required": False,
        "suggested_command": None,
        "retrain_input_template": None,
        "required_manual_fields": [],
        "followup_commands": ["pio phase8-evidence-plan"],
        "operator_blockers": [],
        "reasons": [],
    }


def _receipt(production: Path, database: Path) -> dict:
    artifacts = []
    return {
        "receipt_sha256": "a" * 64,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_after": _sha(database.read_bytes()),
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
        "research_artifacts_after": artifacts,
        "model_id": MODEL_ID,
        "active_cycle_id": CYCLE_ID,
        "model_status_after": "PAPER_CHALLENGER",
        "cycle_status_after": "PAPER_CHALLENGER",
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    status_value: dict | None = None,
    plan_value: dict | None = None,
    handoff_value: dict | None = None,
    artifacts_override: list[dict] | None = None,
    database_state_override: dict | None = None,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    conn = sqlite3.connect(database)
    try:
        conn.execute("CREATE TABLE marker(value TEXT)")
        conn.execute("INSERT INTO marker(value) VALUES ('paper')")
        conn.commit()
    finally:
        conn.close()

    receipt = _receipt(production, database)
    receipt_path = _write(root / "receipt.json", receipt)
    artifacts = (
        copy.deepcopy(artifacts_override)
        if artifacts_override is not None
        else []
    )

    class FakeStorage:
        def __init__(self, path):
            self.path = Path(path)

    runtime = {
        "Storage": FakeStorage,
        "evaluate_phase8_evidence_status": lambda storage: _Record(
            status_value if status_value is not None else _status()
        ),
        "build_phase8_evidence_plan": lambda storage: _Record(
            plan_value if plan_value is not None else _plan()
        ),
        "build_phase8_operator_handoff": lambda storage: _Record(
            handoff_value if handoff_value is not None else _handoff()
        ),
    }

    class FakeAudit:
        @staticmethod
        def _load_reviewed(source):
            return None, None, runtime

    class FakeReadiness:
        @staticmethod
        def _load_reviewed(source):
            return FakeAudit, None, None

    class FakeExecutor:
        @staticmethod
        def validate_phase8_paper_challenger_transition_execution_receipt(
            value,
        ):
            assert isinstance(value, dict)

        @staticmethod
        def _production_database(production_path):
            return (
                Path(production_path) / "data" / "pio.db"
            ).resolve()

        @staticmethod
        def _database_state(database_path):
            if database_state_override is not None:
                return copy.deepcopy(database_state_override)
            database_path = Path(database_path)
            return {
                "database": _sha(database_path.read_bytes()),
                "wal": None,
                "shm": None,
            }

        @staticmethod
        def _load_readiness_module(source):
            return FakeReadiness

        @staticmethod
        def _research_artifacts(source, readiness, data_root):
            return copy.deepcopy(artifacts)

    monkeypatch.setattr(
        MODULE,
        "_load_executor",
        lambda source: FakeExecutor,
    )

    def run():
        return MODULE.build_phase8_paper_challenger_transition_post_audit(
            repository=production,
            source_tree=ROOT,
            execution_receipt_path=receipt_path,
        )

    return temp, database, run


def _reseal(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["post_audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_executor_is_exactly_pinned():
    path = ROOT / MODULE.EXECUTOR_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.EXECUTOR_TOOL
    ]


def test_post_audit_routes_paper_challenger_to_manual_evidence_review(
    monkeypatch,
):
    temp, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["transition_receipt_valid"] is True
    assert report["model_transition_verified"] is True
    assert report["cycle_sync_verified"] is True
    assert report["model_cycle_binding_verified"] is True
    assert report["paper_challenger_active"] is True
    assert report["next_debt_type"] == (
        "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
    )
    assert report["next_scope"] == MODEL_ID
    assert report["continuation_route"] == (
        "PHASE8_PAPER_CHALLENGER_EVIDENCE_REVIEW"
    )
    assert report["paper_account_required"] is True
    assert report["challenger_closed_trade_evidence_required"] is True
    assert report["incumbent_closed_trade_evidence_required"] is True
    assert report["requires_manual_paper_evidence_inputs"] is True
    assert report["requires_separate_paper_evidence_action"] is True
    assert report["paper_evidence_collection_authorized"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["new_live_capital_authorized"] is False
    assert report["phase8_promotion_authorized"] is False


def test_fresh_cycle_status_drift_fails_closed(monkeypatch):
    temp, _, run = _build(
        monkeypatch,
        status_value=_status(cycle_status="OFFLINE_QUALIFIED"),
    )
    try:
        with pytest.raises(ValueError, match="cycle is not PAPER_CHALLENGER"):
            run()
    finally:
        temp.cleanup()


def test_fresh_model_identity_drift_fails_closed(monkeypatch):
    temp, _, run = _build(
        monkeypatch,
        status_value=_status(model_id="challenger-2"),
    )
    try:
        with pytest.raises(ValueError, match="challenger differs"):
            run()
    finally:
        temp.cleanup()


def test_wrong_next_debt_fails_closed(monkeypatch):
    temp, _, run = _build(
        monkeypatch,
        plan_value=_plan(debt_type="PHASE8_PROMOTION_REVIEW"),
    )
    try:
        with pytest.raises(ValueError, match="did not route"):
            run()
    finally:
        temp.cleanup()


def test_automatic_paper_evidence_handoff_fails_closed(monkeypatch):
    temp, _, run = _build(
        monkeypatch,
        handoff_value=_handoff(automatic=True),
    )
    try:
        with pytest.raises(ValueError, match="must not be automatic"):
            run()
    finally:
        temp.cleanup()


def test_database_drift_after_receipt_fails_closed(monkeypatch):
    temp, _, run = _build(
        monkeypatch,
        database_state_override={
            "database": "f" * 64,
            "wal": None,
            "shm": None,
        },
    )
    try:
        with pytest.raises(ValueError, match="changed after"):
            run()
    finally:
        temp.cleanup()


def test_research_artifact_drift_after_receipt_fails_closed(
    monkeypatch,
):
    temp, _, run = _build(
        monkeypatch,
        artifacts_override=[
            {
                "path": "phase8_ml_artifacts/cycle-1/model.json",
                "size_bytes": 10,
                "sha256": "1" * 64,
            }
        ],
    )
    try:
        with pytest.raises(ValueError, match="artifacts changed after"):
            run()
    finally:
        temp.cleanup()


def test_resealed_audit_cannot_authorize_paper_trading(monkeypatch):
    temp, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_trading_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_phase8_paper_challenger_transition_post_audit(
            report
        )


def test_resealed_audit_cannot_authorize_paper_evidence_collection(
    monkeypatch,
):
    temp, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_evidence_collection_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_evidence_collection_authorized=false",
    ):
        MODULE.validate_phase8_paper_challenger_transition_post_audit(
            report
        )


def test_post_audit_has_no_transition_paper_trade_or_live_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "UPDATE model_registry" not in source
    assert "UPDATE continuous_learning_cycles" not in source
    assert "paper-run" not in source
    assert "paper-chain-run" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
