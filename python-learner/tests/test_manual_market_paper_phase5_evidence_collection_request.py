from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_phase5_evidence_collection_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_phase5_evidence_collection_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _plan() -> dict:
    return {
        "collection_plan_sha256": "1" * 64,
        "account": "pio-proof-1",
        "run_id": "manual-proof-20260928-1",
        "collection_plan_ready": True,
        "phase5_promotion_ready": False,
        "recurring_scheduler_evidence_needed": True,
        "entry_diversity_evidence_needed": True,
        "phase3_dependency_blocked": False,
        "historical_failure_streak_blocked": False,
        "ledger_passing": True,
        "minimum_nominal_collection_hours": 60.0,
        "scheduler_service_blob": "2" * 40,
        "scheduler_timer_blob": "3" * 40,
        "scheduler_cli_blob": "4" * 40,
        "scheduler_module_blob": "5" * 40,
    }


def _write_plan(root: Path, plan: dict) -> Path:
    path = root / "phase5-collection-plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def _build(monkeypatch, *, plan: dict | None = None) -> dict:
    value = copy.deepcopy(plan if plan is not None else _plan())

    class FakePlanModule:
        @staticmethod
        def validate_phase5_evidence_collection_plan(candidate):
            assert isinstance(candidate, dict)

    monkeypatch.setattr(
        MODULE,
        "_load_plan_module",
        lambda source: FakePlanModule,
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        return MODULE.build_phase5_evidence_collection_request(
            source_tree=ROOT,
            collection_plan_path=_write_plan(root, value),
        )


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_collection_plan_tool_is_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_request_binds_safe_scheduler_scope_without_authorizing_timer(monkeypatch):
    request = _build(monkeypatch)

    assert request["authorization_scope"] == MODULE.AUTHORIZATION_SCOPE
    assert request["scheduler_interval_seconds"] == 300
    assert request["scheduler_lease_seconds"] == 900
    assert request["scheduler_extra_args"] == [
        "--max-positions",
        "3",
        "--refresh-jupiter-quotes",
    ]
    assert request["scheduler_extra_args_text"] == (
        "--max-positions 3 --refresh-jupiter-quotes"
    )
    assert request["max_positions"] == 3
    assert request["refresh_jupiter_quotes"] is True
    assert request["requested_collection_seconds"] == 60 * 3600
    assert request["requested_collection_hours"] == 60.0
    assert request["nominal_timer_slots"] == 720
    assert request["automatic_stop_required"] is True
    assert request["collection_authorization_present"] is False
    assert request["collection_execution_authorized"] is False
    assert request["paper_timer_enable_authorized"] is False
    assert request["live_capital_authorized"] is False


def test_request_has_one_hour_floor_for_small_remaining_window(monkeypatch):
    plan = _plan()
    plan["minimum_nominal_collection_hours"] = 0.2

    request = _build(monkeypatch, plan=plan)

    assert request["requested_collection_seconds"] == 3600
    assert request["requested_collection_hours"] == 1.0
    assert request["nominal_timer_slots"] == 12


def test_request_rejects_collection_over_72_hours(monkeypatch):
    plan = _plan()
    plan["minimum_nominal_collection_hours"] = 72.0001

    with pytest.raises(ValueError, match="exceeds 72-hour maximum"):
        _build(monkeypatch, plan=plan)


def test_request_rejects_when_phase5_is_already_ready(monkeypatch):
    plan = _plan()
    plan["phase5_promotion_ready"] = True

    with pytest.raises(ValueError, match="already ready"):
        _build(monkeypatch, plan=plan)


def test_request_rejects_when_scheduler_evidence_is_not_needed(monkeypatch):
    plan = _plan()
    plan["recurring_scheduler_evidence_needed"] = False

    with pytest.raises(ValueError, match="scheduler evidence is not needed"):
        _build(monkeypatch, plan=plan)


def test_request_rejects_phase3_dependency_block(monkeypatch):
    plan = _plan()
    plan["phase3_dependency_blocked"] = True

    with pytest.raises(ValueError, match="blocked on persistent Phase 3 promotion"):
        _build(monkeypatch, plan=plan)


def test_request_rejects_historical_failure_streak_block(monkeypatch):
    plan = _plan()
    plan["historical_failure_streak_blocked"] = True

    with pytest.raises(ValueError, match="cannot repair the historical"):
        _build(monkeypatch, plan=plan)


def test_request_rejects_nonpassing_ledger(monkeypatch):
    plan = _plan()
    plan["ledger_passing"] = False

    with pytest.raises(ValueError, match="passing PAPER ledger"):
        _build(monkeypatch, plan=plan)


def test_resealed_request_cannot_enable_timer(monkeypatch):
    request = _build(monkeypatch)
    request["paper_timer_enable_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_phase5_evidence_collection_request(request)


def test_resealed_request_cannot_authorize_collection_execution(monkeypatch):
    request = _build(monkeypatch)
    request["collection_execution_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="collection_execution_authorized=false"):
        MODULE.validate_phase5_evidence_collection_request(request)


def test_resealed_request_cannot_expand_scheduler_args(monkeypatch):
    request = _build(monkeypatch)
    request["scheduler_extra_args"].extend(["--max-positions", "100"])
    request["scheduler_extra_args_text"] = " ".join(
        request["scheduler_extra_args"]
    )
    _reseal(request)

    with pytest.raises(ValueError, match="scheduler args mismatch"):
        MODULE.validate_phase5_evidence_collection_request(request)


def test_resealed_request_cannot_authorize_new_market_entry(monkeypatch):
    request = _build(monkeypatch)
    request["new_market_entry_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="new_market_entry_authorized=false"):
        MODULE.validate_phase5_evidence_collection_request(request)


def test_collection_request_tool_has_no_execution_or_activation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "subprocess" not in source
    assert "systemctl" not in source
    assert "persist_phase5_promotion" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"collection_authorization_present": False' in source
    assert '"collection_execution_authorized": False' in source
    assert '"new_market_entry_authorized": False' in source
    assert '"phase5_promotion_persisted": False' in source
    assert '"recurring_paper_automation_authorized": False' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
