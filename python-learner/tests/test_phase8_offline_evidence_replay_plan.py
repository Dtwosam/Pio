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
    / "build_phase8_offline_evidence_replay_plan.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_offline_evidence_replay_plan",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _FakeHandoff:
    @staticmethod
    def validate_phase8_post_phase7_handoff(value):
        assert isinstance(value, dict)


class _FakeStorage:
    def __init__(self, path):
        self.path = Path(path)


def _handoff(database: Path) -> dict:
    return {
        "handoff_sha256": "a" * 64,
        "pio_database_path": str(database),
        "pio_database_sha256_after": "b" * 64,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
        "phase7_promotion_confirmed": True,
        "phase7_promotion_persisted": True,
        "phase8_handoff_ready": True,
        "phase8_research_only": True,
        "phase8_read_only": True,
        "phase8_policy_actionable": False,
        "phase8_execution_wired": False,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_policy_action_authorized": False,
        "phase8_execution_authorized": False,
        "phase8_promotion_authorized": False,
        "production_pio_database_modified": False,
    }


class _ReplayValue:
    def __init__(self, value):
        self.value = value

    def to_record(self):
        return copy.deepcopy(self.value)


def _replay_record() -> dict:
    return {
        "research_only": True,
        "offline_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "status": "MANUAL_REQUIRED",
        "max_steps": 4,
        "steps_attempted": 1,
        "steps_progressed": 0,
        "terminal_debt_type": "PAPER_CHALLENGER_START_REQUIRED",
        "terminal_scope": "challenger-1",
        "promotion_ready": False,
        "persisted_phase8_current": False,
        "steps": [],
        "reasons": ["operator action remains separate"],
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    db_sha: str = "b" * 64,
    replay_override: dict | None = None,
    mutate_private: bool = True,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        data = production / "data"
        data.mkdir(parents=True)
        database = data / "pio.db"
        database.write_bytes(b"production")
        handoff = _handoff(database)
        handoff_path = _write(root / "handoff.json", handoff)

        replay = _replay_record()
        if replay_override:
            replay.update(replay_override)

        def run(storage, *, max_steps):
            value = copy.deepcopy(replay)
            value["max_steps"] = max_steps
            if mutate_private:
                storage.path.write_bytes(b"private-after")
                artifact = storage.path.parent / "phase8_ml_artifacts" / "x"
                artifact.mkdir(parents=True)
                (artifact / "model.bin").write_bytes(b"model")
            return _ReplayValue(value)

        runtime = {
            "Storage": _FakeStorage,
            "run_phase8_evidence_until_blocked": run,
        }
        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeHandoff, runtime),
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
            lambda source, destination: destination.write_bytes(b"private-before"),
        )

        return MODULE.build_phase8_offline_replay_plan(
            repository=production,
            source_tree=ROOT,
            phase8_handoff_path=handoff_path,
            expected_phase8_handoff_sha256=handoff["handoff_sha256"],
            max_steps=4,
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["replay_plan_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_private_replay_can_progress_without_authorizing_production(monkeypatch):
    report = _build(monkeypatch)

    assert report["production_database_unchanged"] is True
    assert report["private_database_changed"] is True
    assert report["private_artifact_count"] == 1
    assert report["private_artifacts"][0]["path"] == (
        "phase8_ml_artifacts/x/model.bin"
    )
    assert report["replay_status"] == "MANUAL_REQUIRED"
    assert report["steps_attempted"] == 1
    assert report["steps_progressed"] == 0
    assert report["terminal_debt_type"] == (
        "PAPER_CHALLENGER_START_REQUIRED"
    )
    assert report["replay_plan_ready"] is True
    assert report["replay_is_advisory_only"] is True
    assert report["separate_phase8_mutation_action_required"] is True
    assert report["paper_trading_authorized"] is False
    assert report["phase8_execution_authorized"] is False
    assert report["phase8_promotion_authorized"] is False
    assert report["production_pio_database_modified"] is False


def test_production_database_drift_after_handoff_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="changed after Phase 8 handoff",
    ):
        _build(monkeypatch, db_sha="f" * 64)


def test_replay_must_remain_offline_only(monkeypatch):
    with pytest.raises(
        ValueError,
        match="safety boundary changed: offline_only",
    ):
        _build(
            monkeypatch,
            replay_override={"offline_only": False},
        )


def test_replay_must_remain_execution_unwired(monkeypatch):
    with pytest.raises(
        ValueError,
        match="safety boundary changed: execution_wired",
    ):
        _build(
            monkeypatch,
            replay_override={"execution_wired": True},
        )


def test_max_steps_is_bounded(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        data = production / "data"
        data.mkdir(parents=True)
        database = data / "pio.db"
        database.write_bytes(b"production")
        handoff_path = _write(root / "handoff.json", _handoff(database))
        with pytest.raises(ValueError, match="1..8"):
            MODULE.build_phase8_offline_replay_plan(
                repository=production,
                source_tree=ROOT,
                phase8_handoff_path=handoff_path,
                expected_phase8_handoff_sha256="a" * 64,
                max_steps=9,
            )


def test_resealed_plan_cannot_authorize_paper_trading(monkeypatch):
    report = _build(monkeypatch)
    report["paper_trading_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_phase8_offline_replay_plan(report)


def test_resealed_plan_cannot_authorize_phase8_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase8_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase8_promotion_authorized=false",
    ):
        MODULE.validate_phase8_offline_replay_plan(report)


def test_private_paths_are_normalized():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        value = {
            "path": str(root / "phase8_ml_artifacts" / "model.bin"),
            "other": "/opt/pio/data/source.csv",
        }
        normalized = MODULE._normalize_private_paths(value, root)

    assert normalized["path"] == (
        MODULE.PRIVATE_ROOT_TOKEN + "/phase8_ml_artifacts/model.bin"
    )
    assert normalized["other"] == "/opt/pio/data/source.csv"


def test_replay_tool_has_no_production_or_live_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "mode=ro" in source
    assert "_snapshot_sqlite(database, private_db)" in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert '"paper_trading_authorized": False' in source
    assert '"phase8_execution_authorized": False' in source
    assert '"phase8_promotion_authorized": False' in source
    assert '"production_pio_database_modified": False' in source
