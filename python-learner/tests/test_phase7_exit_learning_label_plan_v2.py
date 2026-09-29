from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase7_controlled_live_exit_learning_label_plan_v2.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_learning_label_plan_v2",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


POSITION = "33333333333333333333333333333333"
POOL = "11111111111111111111111111111111"
OPENED = "11111111-2222-4333-8444-555555555555"
EXIT = "01234567-89ab-4def-8123-456789abcdef"
SETTLEMENT = "PHASE7_EXIT_SETTLEMENT:" + "c" * 64


class _FakeAuditModule:
    @staticmethod
    def validate_exit_post_valuation_audit(value):
        assert isinstance(value, dict)


class _FakeStorage:
    def __init__(self, path):
        self.path = Path(path)


@dataclass
class _Label:
    position_address: str = POSITION
    decision_id: str = OPENED
    pool_address: str = POOL
    model_version: str = "m1"
    strategy: str = "s1"
    min_bin_id: int = 1
    max_bin_id: int = 3
    range_width_bins: int = 3
    proposed_capital_quote: str = "100"
    expected_net_return_pct: str = "1.2"
    expected_downside_pct: str = "0.5"
    realized_pnl_quote: str = "4"
    realized_return_bps: int = 400
    prediction_error_bps: int = 280
    target_positive_return: int = 1
    quote_unit: str = "ACCOUNT_QUOTE"
    opened_signature: str = "enter-sig"
    closed_decision_id: str = SETTLEMENT

    def to_record(self):
        return self.__dict__.copy()


def _audit(database: Path) -> dict:
    return {
        "audit_sha256": "a" * 64,
        "opened_decision_id": OPENED,
        "principal_exit_decision_id": EXIT,
        "settlement_decision_id": SETTLEMENT,
        "pool_address": POOL,
        "position_address": POSITION,
        "pio_database_path": str(database),
        "pio_database_sha256_after": "b" * 64,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
        "valuation_present": True,
        "persisted_valuation_matches_apply": True,
        "learning_label_reconciliation_required": True,
        "learning_label_reconciliation_ready": True,
        "phase7_evidence_status_recheck_required": True,
        "requires_separate_phase7_promotion_action": True,
        "post_valuation_audit_ready": True,
        "learning_label_present": False,
        "new_live_entry_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_pio_database_modified": False,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    db_sha: str = "b" * 64,
    pre_label=None,
    opened_decision: str = OPENED,
    closed_decision: str = SETTLEMENT,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        database.write_bytes(b"production")
        audit = _audit(database)
        audit_path = _write(root / "audit.json", audit)

        label = _Label().to_record()
        states = [
            {
                "status": "CLOSED",
                "opened_decision_id": opened_decision,
                "closed_decision_id": closed_decision,
                "valuation_present": True,
                "learning_label": pre_label,
            },
            {
                "status": "CLOSED",
                "opened_decision_id": OPENED,
                "closed_decision_id": SETTLEMENT,
                "valuation_present": True,
                "learning_label": label,
            },
        ]
        calls = {"count": 0}

        def fake_label_state(database_path, *, position_address):
            index = min(calls["count"], len(states) - 1)
            calls["count"] += 1
            return states[index]

        runtime = {
            "Storage": _FakeStorage,
            "build_live_learning_label": (
                lambda storage, **kwargs: type(
                    "Result",
                    (),
                    {
                        "label": _Label(),
                        "reused_existing": False,
                    },
                )()
            ),
        }

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeAuditModule, runtime),
        )
        monkeypatch.setattr(
            MODULE,
            "_database_state",
            lambda path: {"database": db_sha, "wal": None, "shm": None},
        )
        monkeypatch.setattr(
            MODULE,
            "_snapshot_sqlite",
            lambda source, destination: shutil.copyfile(source, destination),
        )
        monkeypatch.setattr(MODULE, "_label_state", fake_label_state)

        return MODULE.build_exit_learning_label_plan_v2(
            source_tree=ROOT,
            saved_post_valuation_audit_path=audit_path,
            expected_post_valuation_audit_sha256=audit["audit_sha256"],
            pio_database_path=database,
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["plan_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_private_replay_builds_exact_new_learning_label(monkeypatch):
    report = _build(monkeypatch)

    assert report["pre_learning_label_present"] is False
    assert report["planned_actions"] == [MODULE.ACTION_BUILD_LABEL]
    assert report["private_replay_succeeded"] is True
    assert report["private_learning_label"]["position_address"] == POSITION
    assert report["private_learning_label"]["decision_id"] == OPENED
    assert report["private_learning_label"]["closed_decision_id"] == SETTLEMENT
    assert report["learning_label_reused_existing"] is False
    assert report["requires_separate_learning_label_apply"] is True
    assert report["production_pio_database_modified"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["phase7_promotion_authorized"] is False


def test_database_must_match_post_valuation_audit(monkeypatch):
    with pytest.raises(
        ValueError,
        match="changed after post-valuation audit",
    ):
        _build(monkeypatch, db_sha="f" * 64)


def test_preexisting_label_is_rejected(monkeypatch):
    with pytest.raises(
        ValueError,
        match="unexpected existing label",
    ):
        _build(monkeypatch, pre_label=_Label().to_record())


def test_opening_decision_binding_must_match(monkeypatch):
    with pytest.raises(
        ValueError,
        match="opening decision differs",
    ):
        _build(monkeypatch, opened_decision="different-open")


def test_closing_decision_binding_must_match(monkeypatch):
    with pytest.raises(
        ValueError,
        match="closing decision differs",
    ):
        _build(monkeypatch, closed_decision="different-close")


def test_resealed_plan_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_learning_label_plan_v2(report)


def test_resealed_plan_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_learning_label_plan_v2(report)


def test_learning_label_plan_is_private_replay_only():
    source = TOOL.read_text(encoding="utf-8")

    assert "mode=ro" in source
    assert "_snapshot_sqlite(database, private_db)" in source
    assert "build_live_learning_label" in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert '"transaction_submission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
    assert '"production_pio_database_modified": False' in source
