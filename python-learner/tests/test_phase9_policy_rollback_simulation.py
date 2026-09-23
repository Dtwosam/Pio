import meteora_learner.phase9_policy_rollback_simulation as rollback_module
from meteora_learner.phase9_policy_rollback_simulation import (
    Phase9PolicyRollbackCriteria,
    Phase9PolicyRollbackMetrics,
    audit_persisted_phase9_policy_rollback_simulation,
    evaluate_phase9_policy_rollback_simulation,
    persist_phase9_policy_rollback_simulation,
)
from meteora_learner.storage import Storage


class DummyRolloutAudit:
    def __init__(self, *, current=True, reasons=()):
        self.current = current
        self.reasons = tuple(reasons)

    def to_record(self):
        return {
            "current": self.current,
            "reasons": list(self.reasons),
        }


def patch_rollout(monkeypatch, *, current=True, reasons=()):
    monkeypatch.setattr(
        rollback_module,
        "audit_persisted_phase9_policy_rollout_simulation",
        lambda storage: DummyRolloutAudit(
            current=current,
            reasons=reasons,
        ),
    )


def criteria():
    return Phase9PolicyRollbackCriteria(
        min_observations=20,
        min_closed_positions=5,
        min_distinct_pools=1,
        max_realized_loss_quote=25.0,
        max_drawdown_pct=2.5,
        min_win_rate_pct=45.0,
        min_mean_return_bps=0.0,
        max_reconciliation_failures=0,
        max_unvalued_closed_positions=0,
        max_stale_decisions=0,
        max_policy_errors=0,
    )


def metrics(
    *,
    observations=25,
    closed_positions=6,
    distinct_pools=1,
    realized_pnl_quote=10.0,
    drawdown_pct=1.0,
    win_rate_pct=60.0,
    mean_return_bps=25.0,
    reconciliation_failures=0,
    unvalued_closed_positions=0,
    stale_decisions=0,
    policy_errors=0,
):
    return Phase9PolicyRollbackMetrics(
        observations=observations,
        closed_positions=closed_positions,
        distinct_pools=distinct_pools,
        realized_pnl_quote=realized_pnl_quote,
        drawdown_pct=drawdown_pct,
        win_rate_pct=win_rate_pct,
        mean_return_bps=mean_return_bps,
        reconciliation_failures=reconciliation_failures,
        unvalued_closed_positions=unvalued_closed_positions,
        stale_decisions=stale_decisions,
        policy_errors=policy_errors,
    )


def test_rollback_simulation_can_clear_when_sample_and_thresholds_pass(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_rollout(monkeypatch)

    result = evaluate_phase9_policy_rollback_simulation(
        storage,
        metrics=metrics(),
        criteria=criteria(),
    )

    assert result.rollout_simulation_current is True
    assert result.sample_sufficient is True
    assert result.rollback_required is False
    assert result.observation_pending is False
    assert result.status == "NO_ROLLBACK_TRIGGER"
    assert result.policy_actionable is False
    assert result.execution_wired is False


def test_rollback_simulation_waits_for_minimum_sample(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_rollout(monkeypatch)

    result = evaluate_phase9_policy_rollback_simulation(
        storage,
        metrics=metrics(
            observations=5,
            closed_positions=1,
        ),
        criteria=criteria(),
    )

    assert result.rollback_required is False
    assert result.observation_pending is True
    assert result.status == "OBSERVATION_PENDING"


def test_hard_loss_or_integrity_breach_requires_rollback_in_simulation(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_rollout(monkeypatch)

    result = evaluate_phase9_policy_rollback_simulation(
        storage,
        metrics=metrics(
            realized_pnl_quote=-30.0,
            reconciliation_failures=1,
        ),
        criteria=criteria(),
    )

    assert result.hard_breach is True
    assert result.rollback_required is True
    assert result.status == "ROLLBACK_REQUIRED"
    assert any(
        "hard rollback trigger:" in reason
        for reason in result.reasons
    )


def test_performance_breach_applies_only_after_sample_depth(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_rollout(monkeypatch)

    early = evaluate_phase9_policy_rollback_simulation(
        storage,
        metrics=metrics(
            observations=5,
            closed_positions=1,
            win_rate_pct=20.0,
            mean_return_bps=-100.0,
        ),
        criteria=criteria(),
    )
    mature = evaluate_phase9_policy_rollback_simulation(
        storage,
        metrics=metrics(
            win_rate_pct=20.0,
            mean_return_bps=-100.0,
        ),
        criteria=criteria(),
    )

    assert early.performance_breach is False
    assert early.rollback_required is False
    assert early.observation_pending is True
    assert mature.performance_breach is True
    assert mature.rollback_required is True


def test_stale_rollout_evidence_requires_rollback_in_simulation(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_rollout(
        monkeypatch,
        current=False,
        reasons=("rollout envelope is stale",),
    )

    result = evaluate_phase9_policy_rollback_simulation(
        storage,
        metrics=metrics(),
        criteria=criteria(),
    )

    assert result.rollback_required is True
    assert result.status == "ROLLBACK_REQUIRED"
    assert any(
        "rollout simulation:" in reason
        for reason in result.reasons
    )


def test_rollback_audit_is_current_when_replay_matches(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_rollout(monkeypatch)
    report = evaluate_phase9_policy_rollback_simulation(
        storage,
        metrics=metrics(),
        criteria=criteria(),
    )
    persist_phase9_policy_rollback_simulation(
        storage,
        report=report,
    )

    audit = audit_persisted_phase9_policy_rollback_simulation(storage)

    assert audit.current is True
    assert audit.persisted_matches_current is True
    assert audit.rollback_required is False
    assert audit.status == "NO_ROLLBACK_TRIGGER"
    assert audit.reasons == ()
