from types import SimpleNamespace

from meteora_learner.baseline_policy import BaselineProposal
from meteora_learner.capital_sizing import CapitalSizingResult
from meteora_learner.phase3_gate import evaluate_phase3_entry_gate
from meteora_learner.pool_safety import PoolSafetyAssessment


def safety(*, accepted=True):
    return PoolSafetyAssessment(
        pool_address="pool",
        name="X-Y",
        token_x_symbol="X",
        token_y_symbol="Y",
        accepted=accepted,
        rejection_reasons=() if accepted else ("pool is blacklisted",),
        tvl_usd=100000,
        volume_24h_usd=20000,
        fees_24h_usd=100,
        dynamic_fee_pct=0.5,
        pool_age_hours=100,
        chain_observations=20,
        standard_spl=True,
        is_blacklisted=False if accepted else True,
    )


def sizing(*, blocked=False):
    return CapitalSizingResult(
        account_equity_quote=10000,
        cash_quote=10000,
        current_deployed_quote=0,
        portfolio_drawdown_bps=0,
        requested_quote=None,
        max_position_quote=1000,
        portfolio_room_quote=7000,
        reserve_room_quote=7000,
        drawdown_multiplier=1.0,
        sized_quote=0.0 if blocked else 1000.0,
        blocked=blocked,
        reasons=("hard drawdown",) if blocked else (),
    )


def baseline(*, phase2_ready):
    proposal = BaselineProposal(
        strategy="SPOT",
        half_width=2,
        center_offset=0,
        decision_active_bin_id=10,
        min_bin_id=8,
        max_bin_id=12,
    )
    return SimpleNamespace(
        pool_address="pool",
        phase2_ready=phase2_ready,
        phase2_blockers=() if phase2_ready else ("Phase 2 evidence incomplete",),
        research_proposal=proposal,
    )


def test_phase3_gate_keeps_good_research_candidate_non_actionable_before_phase2():
    result = evaluate_phase3_entry_gate(
        pool_safety=safety(),
        baseline=baseline(phase2_ready=False),
        sizing=sizing(),
    )

    assert result.research_ready is True
    assert result.entry_authorized is False
    assert result.proposal is not None
    assert "Phase 2 evidence incomplete" in result.reasons


def test_phase3_gate_authorizes_only_when_every_gate_passes():
    result = evaluate_phase3_entry_gate(
        pool_safety=safety(),
        baseline=baseline(phase2_ready=True),
        sizing=sizing(),
    )

    assert result.research_ready is True
    assert result.entry_authorized is True
    assert result.sized_quote == 1000.0


def test_phase3_gate_rejects_unsafe_pool_even_with_phase2_ready():
    result = evaluate_phase3_entry_gate(
        pool_safety=safety(accepted=False),
        baseline=baseline(phase2_ready=True),
        sizing=sizing(),
    )

    assert result.research_ready is False
    assert result.entry_authorized is False
    assert result.proposal is None
    assert any("blacklisted" in reason for reason in result.reasons)


def test_phase3_gate_rejects_zero_size():
    result = evaluate_phase3_entry_gate(
        pool_safety=safety(),
        baseline=baseline(phase2_ready=True),
        sizing=sizing(blocked=True),
    )

    assert result.sizing_ready is False
    assert result.entry_authorized is False
