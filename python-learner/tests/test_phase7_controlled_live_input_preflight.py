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
    / "check_phase7_controlled_live_input_preflight.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_input_preflight",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


POOL = "11111111111111111111111111111111"
WALLET = "22222222222222222222222222222222"


def _plan() -> dict:
    return {
        "plan_sha256": "1" * 64,
        "phase7_evidence_status_sha256": "2" * 64,
        "phase6_post_promotion_audit_sha256": "3" * 64,
        "collection_plan_ready": True,
        "new_entry_evidence_candidate": True,
        "controlled_live_inputs_required": True,
        "phase7_promotion_ready": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
    }


def _config() -> dict:
    return {
        "enabled": True,
        "allowed_pool_addresses": [POOL],
        "max_open_positions": 1,
        "max_rebalances_per_position": 1,
        "max_capital_quote_per_entry": 5.0,
        "max_daily_entry_capital_quote": 5.0,
        "max_daily_entry_submissions": 1,
        "max_daily_realized_loss_quote": 2.0,
        "max_daily_drawdown_pct": 1.0,
        "allow_rebalance": False,
        "allow_exit": True,
    }


def _proposal() -> dict:
    return {
        "decision_id": "11111111-2222-4333-8444-555555555555",
        "mode": "LIVE",
        "action": "ENTER",
        "pool_address": POOL,
        "capital_quote": 5.0,
        "account_equity_quote": 1000.0,
        "portfolio_deployed_quote": 0.0,
        "daily_drawdown_pct": 0.25,
        "min_bin_id": -10,
        "max_bin_id": 10,
        "strategy": "SPOT",
        "expected_net_return_pct": 1.0,
        "expected_downside_pct": 0.5,
        "model_version": "baseline-v1",
        "data_age_seconds": 5,
    }


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    plan: dict | None = None,
    config: dict | None = None,
    proposal: dict | None = None,
    wallet: str = WALLET,
) -> dict:
    plan_value = copy.deepcopy(plan if plan is not None else _plan())
    config_value = copy.deepcopy(config if config is not None else _config())
    proposal_value = copy.deepcopy(
        proposal if proposal is not None else _proposal()
    )

    class FakePlan:
        @staticmethod
        def validate_phase7_evidence_plan(value):
            assert isinstance(value, dict)

    monkeypatch.setattr(MODULE, "_load_plan_module", lambda source: FakePlan)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        return MODULE.build_phase7_input_preflight(
            source_tree=ROOT,
            phase7_evidence_plan_path=_write(
                root, "plan.json", plan_value
            ),
            controlled_live_config_path=_write(
                root, "config.json", config_value
            ),
            proposal_path=_write(
                root, "proposal.json", proposal_value
            ),
            executor_wallet_pubkey=wallet,
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["input_preflight_sha256"] = MODULE._sha256(identity)


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_preflight_binds_exact_operator_inputs_without_authorizing_live(
    monkeypatch,
):
    report = _build(monkeypatch)

    assert report["phase7_evidence_plan_sha256"] == "1" * 64
    assert report["controlled_live_config"]["allowed_pool_addresses"] == [POOL]
    assert report["proposal"]["pool_address"] == POOL
    assert report["proposal"]["capital_quote"] == 5.0
    assert report["executor_wallet_pubkey"] == WALLET
    assert report["single_pool_scope"] is True
    assert report["single_position_scope"] is True
    assert report["single_daily_entry_scope"] is True
    assert report["rebalance_disabled"] is True
    assert report["exit_enabled"] is True
    assert report["input_preflight_ready"] is True
    assert report["controlled_live_authorization_present"] is False
    assert report["controlled_live_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_preflight_requires_plan_to_allow_new_entry_evidence(monkeypatch):
    plan = _plan()
    plan["new_entry_evidence_candidate"] = False

    with pytest.raises(ValueError, match="does not permit new entry evidence"):
        _build(monkeypatch, plan=plan)


def test_preflight_rejects_placeholder_pool(monkeypatch):
    config = _config()
    config["allowed_pool_addresses"] = ["REPLACE_WITH_APPROVED_POOL"]

    with pytest.raises(ValueError):
        _build(monkeypatch, config=config)


def test_preflight_rejects_multiple_pools(monkeypatch):
    config = _config()
    config["allowed_pool_addresses"] = [POOL, WALLET]

    with pytest.raises(ValueError, match="exactly one pool"):
        _build(monkeypatch, config=config)


def test_preflight_rejects_rebalance_enabled(monkeypatch):
    config = _config()
    config["allow_rebalance"] = True

    with pytest.raises(ValueError, match="disable REBALANCE"):
        _build(monkeypatch, config=config)


def test_preflight_rejects_multiple_daily_entries(monkeypatch):
    config = _config()
    config["max_daily_entry_submissions"] = 2

    with pytest.raises(ValueError, match="max_daily_entry_submissions=1"):
        _build(monkeypatch, config=config)


def test_preflight_rejects_entry_cap_above_exact_proposal(monkeypatch):
    config = _config()
    config["max_capital_quote_per_entry"] = 6.0

    with pytest.raises(ValueError, match="entry cap must equal proposal capital"):
        _build(monkeypatch, config=config)


def test_preflight_rejects_daily_cap_above_exact_proposal(monkeypatch):
    config = _config()
    config["max_daily_entry_capital_quote"] = 6.0

    with pytest.raises(ValueError, match="daily cap must equal proposal capital"):
        _build(monkeypatch, config=config)


def test_preflight_rejects_loss_budget_above_proposal_capital(monkeypatch):
    config = _config()
    config["max_daily_realized_loss_quote"] = 6.0

    with pytest.raises(ValueError, match="loss budget cannot exceed"):
        _build(monkeypatch, config=config)


def test_preflight_rejects_proposal_drawdown_over_config(monkeypatch):
    proposal = _proposal()
    proposal["daily_drawdown_pct"] = 2.0

    with pytest.raises(ValueError, match="drawdown exceeds configured"):
        _build(monkeypatch, proposal=proposal)


def test_preflight_rejects_wallet_equal_to_pool(monkeypatch):
    with pytest.raises(ValueError, match="wallet cannot equal pool"):
        _build(monkeypatch, wallet=POOL)


def test_resealed_preflight_cannot_authorize_controlled_live(monkeypatch):
    report = _build(monkeypatch)
    report["controlled_live_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="controlled_live_authorized=false"):
        MODULE.validate_phase7_input_preflight(report)


def test_resealed_preflight_cannot_authorize_signing(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_signing_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_signing_authorized=false",
    ):
        MODULE.validate_phase7_input_preflight(report)


def test_resealed_preflight_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_phase7_input_preflight(report)


def test_preflight_tool_has_no_production_or_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "subprocess" not in source
    assert "systemctl" not in source
    assert "persist_phase7_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert "ssh-keygen" not in source
    assert "PIO_LIVE_SUBMIT_ENABLED" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"controlled_live_authorization_present": False' in source
    assert '"controlled_live_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
