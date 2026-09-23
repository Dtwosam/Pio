import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from meteora_learner.execution_contract import (
    ExecutionAction,
    ExecutionMode,
    ExecutionRiskConfig,
    TradeProposal,
)


ROOT = Path(__file__).resolve().parents[2]


def test_shared_trade_proposal_fixture_parses_in_python():
    payload = json.loads(
        (ROOT / "contracts/examples/trade_proposal.live.example.json").read_text()
    )
    proposal = TradeProposal.model_validate(payload)

    assert proposal.mode is ExecutionMode.LIVE
    assert proposal.action is ExecutionAction.ENTER
    assert proposal.strategy == "SPOT"
    assert proposal.to_record()["decision_id"] == payload["decision_id"]


def test_shared_risk_config_fixture_parses_in_python():
    payload = json.loads(
        (ROOT / "contracts/examples/risk_config.example.json").read_text()
    )
    config = ExecutionRiskConfig.model_validate(payload)

    assert config.max_capital_per_position_pct == 2.0
    assert config.max_data_age_seconds == 30


def test_execution_contract_rejects_non_finite_values():
    payload = json.loads(
        (ROOT / "contracts/examples/trade_proposal.live.example.json").read_text()
    )
    payload["capital_quote"] = float("nan")

    with pytest.raises(ValidationError):
        TradeProposal.model_validate(payload)
