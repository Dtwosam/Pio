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
    / "check_phase7_controlled_live_exit_learning_label_post_audit.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_learning_label_post_audit",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


POSITION = "33333333333333333333333333333333"
POOL = "11111111111111111111111111111111"
SETTLEMENT_DECISION_ID = "PHASE7_EXIT_SETTLEMENT:" + "c" * 64

VALUATION = {
    "position_address": POSITION,
    "pool_address": POOL,
    "opened_decision_id": "11111111-2222-4333-8444-555555555555",
    "closed_decision_id": SETTLEMENT_DECISION_ID,
    "realized_pnl_quote": "3",
    "realized_return_bps": 300,
}
LABEL = {
    "position_address": POSITION,
    "decision_id": "11111111-2222-4333-8444-555555555555",
    "closed_decision_id": SETTLEMENT_DECISION_ID,
    "realized_pnl_quote": "3",
    "realized_return_bps": 300,
}


class _Planner:
    digest = "d" * 64
    target_digest = "3" * 64

    @staticmethod
    def _load_json(path, *, label):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def _database_state(cls, database):
        return {"database": cls.digest, "wal": None, "shm": None}

    @staticmethod
    def _target_state(database, position_address):
        return {
            "outcome": {
                "position_address": POSITION,
                "label_status": "VALUED",
            },
            "valuation": {
                "position_address": POSITION,
                "closed_decision_id": SETTLEMENT_DECISION_ID,
                "raw_json": json.dumps(
                    VALUATION,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
            "learning_label": {
                "position_address": POSITION,
                "decision_id": LABEL["decision_id"],
                "closed_decision_id": SETTLEMENT_DECISION_ID,
                "raw_json": json.dumps(
                    LABEL,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        }

    @staticmethod
    def _canonical_bytes(value):
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")


class _Apply:
    @staticmethod
    def _load_plan(source):
        return _Planner

    @staticmethod
    def validate_exit_learning_label_apply(value):
        assert isinstance(value, dict)


def _target_sha() -> str:
    return hashlib.sha256(
        _Planner._canonical_bytes(
            _Planner._target_state(Path("/tmp/pio.db"), POSITION)
        )
    ).hexdigest()


def _applied(database: Path) -> dict:
    return {
        "apply_sha256": "a" * 64,
        "saved_plan_sha256": "b" * 64,
        "opened_decision_id": LABEL["decision_id"],
        "principal_exit_decision_id": (
            "01234567-89ab-4def-8123-456789abcdef"
        ),
        "settlement_decision_id": SETTLEMENT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "pio_database_path": str(database),
        "pio_database_sha256_after": "d" * 64,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
        "expected_target_state_sha256": _target_sha(),
        "valuation": dict(VALUATION),
        "learning_label": dict(LABEL),
        "target_state_matches_plan": True,
        "learning_label_apply_complete": True,
        "learning_label_reconciliation_remaining": False,
        "requires_post_label_audit": True,
        "phase7_evidence_status_recheck_required": True,
        "requires_separate_phase7_promotion_action": True,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    digest="d" * 64,
    target_override=None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production")
        applied = _applied(database)
        path = _write(root / "apply.json", applied)

        _Planner.digest = digest
        monkeypatch.setattr(MODULE, "_load_apply", lambda source: _Apply)
        if target_override is not None:
            monkeypatch.setattr(
                _Planner,
                "_target_state",
                staticmethod(lambda database, position_address: target_override),
            )

        return MODULE.build_exit_learning_label_post_audit(
            source_tree=ROOT,
            saved_apply_path=path,
            expected_apply_sha256=applied["apply_sha256"],
            pio_database_path=database,
        )


def _reseal(report):
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_apply_dependency_is_exactly_pinned():
    path = ROOT / MODULE.APPLY_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.APPLY_TOOL
    ]


def test_post_label_audit_requires_exact_persisted_records(monkeypatch):
    report = _build(monkeypatch)

    assert report["outcome_label_status"] == "VALUED"
    assert report["valuation"] == VALUATION
    assert report["learning_label"] == LABEL
    assert report["target_state_matches_apply"] is True
    assert report["valuation_matches_apply"] is True
    assert report["learning_label_matches_apply"] is True
    assert report["learning_label_post_audit_ready"] is True
    assert report["phase7_evidence_status_recheck_required"] is True
    assert report["requires_separate_phase7_promotion_action"] is True
    assert report["transaction_submission_authorized"] is False
    assert report["phase7_promotion_authorized"] is False
    assert report["production_pio_database_modified"] is False


def test_database_drift_after_label_apply_fails(monkeypatch):
    with pytest.raises(ValueError, match="changed after learning-label apply"):
        _build(monkeypatch, digest="f" * 64)


def test_target_drift_after_label_apply_fails(monkeypatch):
    with pytest.raises(ValueError, match="target differs"):
        _build(
            monkeypatch,
            target_override={
                "outcome": {"label_status": "VALUED"},
                "valuation": None,
                "learning_label": None,
            },
        )


def test_resealed_audit_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_learning_label_post_audit(report)


def test_resealed_audit_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_learning_label_post_audit(report)


def test_post_label_audit_is_read_only():
    source = TOOL.read_text(encoding="utf-8")

    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "INSERT INTO live_" not in source
    assert "UPDATE live_" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
    assert '"production_pio_database_modified": False' in source
