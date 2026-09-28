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
    / "build_manual_market_paper_phase5_evidence_collection_plan.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_phase5_evidence_collection_plan",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CRITERIA = {
    "min_runtime_hours": 72.0,
    "min_terminal_ticks": 500,
    "min_success_rate_pct": 99.0,
    "max_dependency_blocked_pct": 5.0,
    "max_consecutive_failures": 1,
    "max_stale_running_ticks": 0,
    "stale_running_after_seconds": 900,
    "min_applied_chain_valuations": 100,
    "min_distinct_positions_valued": 3,
    "min_closed_positions": 3,
    "min_distinct_pools": 2,
}


def _status(*, ready: bool = False, failure_streak: int = 0) -> dict:
    if ready:
        endurance = {
            "runtime_hours": 72.0,
            "terminal_ticks": 500,
            "success_rate_pct": 99.5,
            "dependency_blocked_pct": 2.0,
            "max_observed_consecutive_failures": 1,
            "stale_running_ticks": 0,
            "applied_chain_valuations": 100,
            "distinct_positions_valued": 3,
        }
        closed_positions = 3
        distinct_pools = 2
        reasons = []
        phase3_promoted = True
    else:
        endurance = {
            "runtime_hours": 12.0,
            "terminal_ticks": 100,
            "success_rate_pct": 98.0,
            "dependency_blocked_pct": 6.0,
            "max_observed_consecutive_failures": failure_streak,
            "stale_running_ticks": 0,
            "applied_chain_valuations": 20,
            "distinct_positions_valued": 1,
        }
        closed_positions = 1
        distinct_pools = 1
        reasons = [
            "endurance: runtime hours below reviewed minimum",
            "closed positions below reviewed minimum",
        ]
        phase3_promoted = True

    return {
        "phase5_evidence_status_ready": True,
        "phase5_evidence_status_sha256": "1" * 64,
        "account": "pio-proof-1",
        "run_id": "manual-proof-20260928-1",
        "phase5_promotion_ready": ready,
        "phase5_reasons": reasons,
        "phase5_criteria": copy.deepcopy(CRITERIA),
        "phase3_promoted": phase3_promoted,
        "endurance": endurance,
        "ledger_audit": {
            "account_id": "pio-proof-1",
            "passing": True,
            "position_failures": 0,
            "reasons": [],
        },
        "closed_positions": closed_positions,
        "distinct_valued_pools": distinct_pools,
        "phase5_promotion_persisted": False,
        "phase5_promotion_authorized": False,
        "recurring_paper_automation_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }


def _write_status(root: Path, status: dict) -> Path:
    path = root / "phase5-status.json"
    path.write_text(json.dumps(status), encoding="utf-8")
    return path


def _build(monkeypatch, *, status: dict) -> dict:
    class FakeStatusModule:
        @staticmethod
        def validate_phase5_evidence_status(value):
            assert isinstance(value, dict)

    monkeypatch.setattr(
        MODULE,
        "_load_status_module",
        lambda source: FakeStatusModule,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        return MODULE.build_phase5_evidence_collection_plan(
            source_tree=ROOT,
            phase5_evidence_status_path=_write_status(root, status),
        )


def _reseal(plan: dict) -> None:
    identity = {field: plan[field] for field in MODULE.PLAN_FIELDS}
    plan["collection_plan_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_collection_plan_calculates_exact_current_deficits(monkeypatch):
    plan = _build(monkeypatch, status=_status())

    assert plan["current_evidence"]["runtime_hours"] == 12.0
    assert plan["current_evidence"]["terminal_ticks"] == 100
    assert plan["evidence_deficits"] == {
        "runtime_hours": 60.0,
        "terminal_ticks": 400,
        "applied_chain_valuations": 80,
        "distinct_positions_valued": 2,
        "closed_positions": 2,
        "distinct_valued_pools": 1,
    }
    assert plan["minimum_nominal_collection_hours"] == 60.0
    assert plan["recurring_scheduler_evidence_needed"] is True
    assert plan["entry_diversity_evidence_needed"] is True
    assert plan["health_checks"]["success_rate_meets"] is False
    assert plan["health_checks"]["dependency_blocked_meets"] is False


def test_timer_nominal_hours_uses_larger_of_runtime_or_tick_deficit(monkeypatch):
    status = _status()
    status["endurance"]["runtime_hours"] = 70.0
    status["endurance"]["terminal_ticks"] = 100

    plan = _build(monkeypatch, status=status)

    assert plan["evidence_deficits"]["runtime_hours"] == 2.0
    assert plan["evidence_deficits"]["terminal_ticks"] == 400
    assert plan["minimum_nominal_collection_hours"] == pytest.approx(
        400 * 5 / 60
    )


def test_scheduler_scope_does_not_claim_new_entry_creation(monkeypatch):
    plan = _build(monkeypatch, status=_status())

    assert "open_position_management" in plan["scheduler_can_collect"]
    assert "new_market_entry_creation" in plan["scheduler_cannot_collect"]
    assert plan["requires_separate_manual_entry_authorization"] is True
    assert plan["timer_interval_seconds"] == 300
    assert plan["scheduler_lease_seconds"] == 900


def test_historical_failure_streak_is_called_out(monkeypatch):
    plan = _build(
        monkeypatch,
        status=_status(failure_streak=2),
    )

    assert plan["health_checks"]["consecutive_failures_meet"] is False
    assert plan["historical_failure_streak_blocked"] is True


def test_ready_phase5_plan_still_authorizes_nothing(monkeypatch):
    plan = _build(monkeypatch, status=_status(ready=True))

    assert plan["phase5_promotion_ready"] is True
    assert plan["evidence_deficits"] == {
        "runtime_hours": 0.0,
        "terminal_ticks": 0,
        "applied_chain_valuations": 0,
        "distinct_positions_valued": 0,
        "closed_positions": 0,
        "distinct_valued_pools": 0,
    }
    assert plan["phase5_promotion_persisted"] is False
    assert plan["phase5_promotion_authorized"] is False
    assert plan["recurring_paper_automation_authorized"] is False
    assert plan["paper_timer_enable_authorized"] is False
    assert plan["live_capital_authorized"] is False


def test_phase3_dependency_is_separate_from_paper_evidence(monkeypatch):
    status = _status(ready=False)
    status["phase3_promoted"] = False
    status["phase5_reasons"].append(
        "Phase 3 must be persistently promoted before Phase 5"
    )

    plan = _build(monkeypatch, status=status)

    assert plan["phase3_dependency_blocked"] is True
    assert "phase3_promotion" in plan["scheduler_cannot_collect"]


def test_resealed_plan_cannot_enable_timer(monkeypatch):
    plan = _build(monkeypatch, status=_status())
    plan["paper_timer_enable_authorized"] = True
    _reseal(plan)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_phase5_evidence_collection_plan(plan)


def test_resealed_plan_cannot_persist_phase5_promotion(monkeypatch):
    plan = _build(monkeypatch, status=_status(ready=True))
    plan["phase5_promotion_persisted"] = True
    _reseal(plan)

    with pytest.raises(ValueError, match="phase5_promotion_persisted=false"):
        MODULE.validate_phase5_evidence_collection_plan(plan)


def test_resealed_plan_cannot_claim_scheduler_creates_entries(monkeypatch):
    plan = _build(monkeypatch, status=_status())
    plan["scheduler_cannot_collect"] = [
        "phase3_promotion",
        "phase5_promotion_persistence",
        "live_execution",
    ]
    _reseal(plan)

    with pytest.raises(ValueError, match="scheduler exclusion list mismatch"):
        MODULE.validate_phase5_evidence_collection_plan(plan)


def test_collection_plan_tool_has_no_execution_or_activation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "subprocess" not in source
    assert "systemctl" not in source
    assert "persist_phase5_promotion" not in source
    assert "--persist-ready" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"phase5_promotion_persisted": False' in source
    assert '"recurring_paper_automation_authorized": False' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
