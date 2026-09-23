from dataclasses import replace

import meteora_learner.phase9_policy_rollout_simulation as rollout_module
from meteora_learner.phase9_policy_rollout_simulation import (
    Phase9PolicyRolloutEnvelope,
    Phase9PolicyRolloutSimulationCriteria,
    audit_persisted_phase9_policy_rollout_simulation,
    evaluate_phase9_policy_rollout_simulation,
    persist_phase9_policy_rollout_simulation,
)
from meteora_learner.storage import Storage


class DummyReadiness:
    ready = True
    reasons = ()

    def to_record(self):
        return {
            "ready": True,
            "reasons": [],
            "research_only": True,
            "simulation_only": True,
            "policy_actionable": False,
            "execution_wired": False,
        }


def envelope(
    *,
    enabled=False,
    pools=("pool-a", "pool-b"),
    open_positions=2,
    rebalances=2,
    entry_cap=100.0,
    daily_cap=300.0,
    daily_submissions=3,
    daily_loss=50.0,
    drawdown=5.0,
    allow_rebalance=True,
    allow_exit=True,
):
    return Phase9PolicyRolloutEnvelope(
        enabled=enabled,
        allowed_pool_addresses=tuple(pools),
        max_open_positions=open_positions,
        max_rebalances_per_position=rebalances,
        max_capital_quote_per_entry=entry_cap,
        max_daily_entry_capital_quote=daily_cap,
        max_daily_entry_submissions=daily_submissions,
        max_daily_realized_loss_quote=daily_loss,
        max_daily_drawdown_pct=drawdown,
        allow_rebalance=allow_rebalance,
        allow_exit=allow_exit,
    )


def patch_readiness(monkeypatch):
    monkeypatch.setattr(
        rollout_module,
        "evaluate_phase9_policy_readiness",
        lambda storage: DummyReadiness(),
    )


def test_strictly_narrower_disabled_rollout_can_pass(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_readiness(monkeypatch)
    current = envelope()
    proposed = envelope(
        pools=("pool-a",),
        open_positions=1,
        rebalances=1,
        entry_cap=50.0,
        daily_cap=100.0,
        daily_submissions=1,
        daily_loss=25.0,
        drawdown=2.5,
        allow_rebalance=False,
    )

    result = evaluate_phase9_policy_rollout_simulation(
        storage,
        current=current,
        proposed=proposed,
    )

    assert result.rollout_simulation_ready is True
    assert result.proposal_disabled is True
    assert result.allowed_pools_subset is True
    assert result.numeric_caps_within_current is True
    assert result.action_permissions_compatible is True
    assert result.strictly_narrower is True
    assert result.policy_actionable is False
    assert result.execution_wired is False


def test_rollout_rejects_cap_broader_than_current(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_readiness(monkeypatch)
    current = envelope()
    proposed = envelope(
        pools=("pool-a",),
        entry_cap=150.0,
        allow_rebalance=False,
    )

    result = evaluate_phase9_policy_rollout_simulation(
        storage,
        current=current,
        proposed=proposed,
    )

    assert result.rollout_simulation_ready is False
    assert result.numeric_caps_within_current is False
    assert any(
        "max_capital_quote_per_entry" in reason
        for reason in result.reasons
    )


def test_rollout_rejects_enabled_proposal(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_readiness(monkeypatch)
    current = envelope()
    proposed = envelope(
        enabled=True,
        pools=("pool-a",),
        open_positions=1,
        rebalances=1,
        entry_cap=50.0,
        daily_cap=100.0,
        daily_submissions=1,
        daily_loss=25.0,
        drawdown=2.5,
        allow_rebalance=False,
    )

    result = evaluate_phase9_policy_rollout_simulation(
        storage,
        current=current,
        proposed=proposed,
    )

    assert result.rollout_simulation_ready is False
    assert result.proposal_disabled is False
    assert any(
        "must remain disabled" in reason
        for reason in result.reasons
    )

    forged = replace(
        result,
        rollout_simulation_ready=True,
    )
    try:
        persist_phase9_policy_rollout_simulation(
            storage,
            report=forged,
        )
    except ValueError as exc:
        assert "cannot persist an enabled proposal" in str(exc)
    else:
        raise AssertionError("expected enabled rollout persistence refusal")


def test_rollout_audit_is_current_when_readiness_and_envelope_match(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_readiness(monkeypatch)
    current = envelope()
    proposed = envelope(
        pools=("pool-a",),
        open_positions=1,
        rebalances=1,
        entry_cap=50.0,
        daily_cap=100.0,
        daily_submissions=1,
        daily_loss=25.0,
        drawdown=2.5,
        allow_rebalance=False,
    )
    report = evaluate_phase9_policy_rollout_simulation(
        storage,
        current=current,
        proposed=proposed,
        criteria=Phase9PolicyRolloutSimulationCriteria(),
    )
    persist_phase9_policy_rollout_simulation(
        storage,
        report=report,
    )

    audit = audit_persisted_phase9_policy_rollout_simulation(storage)

    assert audit.current is True
    assert audit.current_rollout_simulation_ready is True
    assert audit.persisted_matches_current is True
    assert audit.reasons == ()
