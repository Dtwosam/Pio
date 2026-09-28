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
    / "build_phase7_controlled_live_evidence_plan.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_evidence_plan",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CRITERIA = {
    "min_closed_positions": 3,
    "min_distinct_pools": 2,
    "min_confirmed_receipts": 6,
    "max_failed_receipts": 0,
    "max_open_positions_at_validation": 0,
}


def _status(
    *,
    ready: bool = False,
    ledger_clean: bool = True,
    confirmed_receipts: int = 0,
    failed_receipts: int = 0,
    closed_positions: int = 0,
    open_positions: int = 0,
    distinct_closed_pools: int = 0,
    valued_closed_positions: int = 0,
    labeled_closed_positions: int = 0,
) -> dict:
    reasons = [] if ready else ["more controlled-live evidence required"]
    return {
        "phase7_evidence_status_ready": True,
        "phase7_evidence_status_sha256": "1" * 64,
        "phase6_post_promotion_audit_sha256": "2" * 64,
        "phase7_criteria": copy.deepcopy(CRITERIA),
        "phase6_promoted": True,
        "ledger_audit": {
            "clean": ledger_clean,
            "reasons": [] if ledger_clean else ["ledger mismatch"],
        },
        "confirmed_receipts": confirmed_receipts,
        "failed_receipts": failed_receipts,
        "closed_positions": closed_positions,
        "open_positions": open_positions,
        "distinct_closed_pools": distinct_closed_pools,
        "valued_closed_positions": valued_closed_positions,
        "labeled_closed_positions": labeled_closed_positions,
        "phase7_promotion_ready": ready,
        "phase7_reasons": reasons,
        "phase7_promotion_persisted": False,
        "phase7_promotion_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
    }


def _write_status(root: Path, value: dict) -> Path:
    path = root / "phase7-status.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, status: dict) -> dict:
    class FakeStatus:
        @staticmethod
        def validate_phase7_evidence_status(value):
            assert isinstance(value, dict)

    monkeypatch.setattr(MODULE, "_load_status_module", lambda source: FakeStatus)
    monkeypatch.setattr(
        MODULE,
        "_validate_example",
        lambda source: {
            "enabled": False,
            "allowed_pool_addresses": ["REPLACE_WITH_APPROVED_POOL"],
            "max_open_positions": 1,
            "max_rebalances_per_position": 3,
            "max_capital_quote_per_entry": 50,
            "max_daily_entry_capital_quote": 100,
            "max_daily_entry_submissions": 3,
            "max_daily_realized_loss_quote": 20,
            "max_daily_drawdown_pct": 2,
            "allow_rebalance": True,
            "allow_exit": True,
        },
    )

    with tempfile.TemporaryDirectory() as tmp:
        return MODULE.build_phase7_evidence_plan(
            source_tree=ROOT,
            phase7_evidence_status_path=_write_status(Path(tmp), status),
        )


def _reseal(plan: dict) -> None:
    identity = {field: plan[field] for field in MODULE.PLAN_FIELDS}
    plan["plan_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_reviewed_example_stays_disabled_placeholder_only():
    example = MODULE._validate_example(ROOT)

    assert example["enabled"] is False
    assert example["allowed_pool_addresses"] == [
        "REPLACE_WITH_APPROVED_POOL"
    ]
    assert example["max_open_positions"] == 1
    assert example["allow_exit"] is True


def test_clean_empty_corpus_can_only_become_operator_owned_entry_candidate(
    monkeypatch,
):
    plan = _build(monkeypatch, _status())

    assert plan["evidence_deficits"] == {
        "confirmed_receipts": 6,
        "closed_positions": 3,
        "distinct_closed_pools": 2,
        "failed_receipts_excess": 0,
        "open_positions_excess": 0,
        "unvalued_closed_positions": 0,
        "unlabeled_closed_positions": 0,
    }
    assert plan["new_entry_evidence_candidate"] is True
    assert plan["controlled_live_inputs_required"] is True
    assert plan["required_operator_inputs"] == list(
        MODULE.REQUIRED_OPERATOR_INPUTS
    )
    assert plan["controlled_live_authorized"] is False
    assert plan["live_submit_authorized"] is False
    assert plan["live_capital_authorized"] is False


def test_failed_receipt_blocks_more_live_entry_risk(monkeypatch):
    plan = _build(
        monkeypatch,
        _status(failed_receipts=1),
    )

    assert plan["historical_failed_receipts_blocked"] is True
    assert plan["new_entry_evidence_candidate"] is False
    assert plan["controlled_live_inputs_required"] is False


def test_open_position_routes_to_exit_reconciliation_before_new_entry(monkeypatch):
    plan = _build(
        monkeypatch,
        _status(open_positions=1),
    )

    assert plan["open_positions_require_exit_reconciliation"] is True
    assert plan["new_entry_evidence_candidate"] is False


def test_dirty_ledger_routes_to_reconciliation_before_new_entry(monkeypatch):
    plan = _build(
        monkeypatch,
        _status(ledger_clean=False),
    )

    assert plan["ledger_reconciliation_required"] is True
    assert plan["new_entry_evidence_candidate"] is False


def test_missing_valuation_or_label_blocks_new_entry(monkeypatch):
    plan = _build(
        monkeypatch,
        _status(
            confirmed_receipts=4,
            closed_positions=2,
            distinct_closed_pools=1,
            valued_closed_positions=1,
            labeled_closed_positions=1,
        ),
    )

    assert plan["valuation_completion_required"] is True
    assert plan["label_completion_required"] is True
    assert plan["evidence_deficits"]["unvalued_closed_positions"] == 1
    assert plan["evidence_deficits"]["unlabeled_closed_positions"] == 1
    assert plan["new_entry_evidence_candidate"] is False


def test_ready_phase7_plan_still_authorizes_nothing(monkeypatch):
    plan = _build(
        monkeypatch,
        _status(
            ready=True,
            confirmed_receipts=6,
            closed_positions=3,
            distinct_closed_pools=2,
            valued_closed_positions=3,
            labeled_closed_positions=3,
        ),
    )

    assert plan["phase7_promotion_ready"] is True
    assert plan["new_entry_evidence_candidate"] is False
    assert plan["controlled_live_inputs_required"] is False
    assert plan["phase7_promotion_persisted"] is False
    assert plan["phase7_promotion_authorized"] is False
    assert plan["controlled_live_authorized"] is False
    assert plan["transaction_signing_authorized"] is False
    assert plan["transaction_submission_authorized"] is False


def test_resealed_plan_cannot_authorize_controlled_live(monkeypatch):
    plan = _build(monkeypatch, _status())
    plan["controlled_live_authorized"] = True
    _reseal(plan)

    with pytest.raises(ValueError, match="controlled_live_authorized=false"):
        MODULE.validate_phase7_evidence_plan(plan)


def test_resealed_plan_cannot_authorize_live_submit(monkeypatch):
    plan = _build(monkeypatch, _status())
    plan["live_submit_authorized"] = True
    _reseal(plan)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase7_evidence_plan(plan)


def test_resealed_plan_cannot_claim_example_is_production_ready(monkeypatch):
    plan = _build(monkeypatch, _status())
    plan["example_config_production_ready"] = True
    _reseal(plan)

    with pytest.raises(ValueError, match="cannot be production-ready"):
        MODULE.validate_phase7_evidence_plan(plan)


def test_plan_tool_has_no_live_execution_or_input_synthesis():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "subprocess" not in source
    assert "systemctl" not in source
    assert "persist_phase7_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"example_config_production_ready": False' in source
    assert '"phase7_promotion_persisted": False' in source
    assert '"controlled_live_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
