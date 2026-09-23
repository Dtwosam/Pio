from dataclasses import replace

import meteora_learner.phase9_policy_readiness as readiness_module
from meteora_learner.phase9_policy_readiness import (
    evaluate_phase9_policy_readiness,
)
from meteora_learner.storage import Storage


class DummyAudit:
    def __init__(self, *, current, reasons=()):
        self.current = current
        self.reasons = tuple(reasons)

    def to_record(self):
        return {
            "current": self.current,
            "reasons": list(self.reasons),
        }


def test_policy_readiness_requires_both_current_gates(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        readiness_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyAudit(current=True),
    )
    monkeypatch.setattr(
        readiness_module,
        "audit_persisted_phase9_policy_controlled_validation",
        lambda storage: DummyAudit(current=True),
    )

    result = evaluate_phase9_policy_readiness(storage)

    assert result.ready is True
    assert result.authorization_current is True
    assert result.controlled_validation_current is True
    assert result.research_only is True
    assert result.simulation_only is True
    assert result.policy_actionable is False
    assert result.execution_wired is False
    assert result.reasons == ()


def test_policy_readiness_fails_closed_when_controlled_validation_is_stale(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        readiness_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyAudit(current=True),
    )
    monkeypatch.setattr(
        readiness_module,
        "audit_persisted_phase9_policy_controlled_validation",
        lambda storage: DummyAudit(
            current=False,
            reasons=("persisted holdout replay is stale",),
        ),
    )

    result = evaluate_phase9_policy_readiness(storage)

    assert result.ready is False
    assert result.authorization_current is True
    assert result.controlled_validation_current is False
    assert any(
        "controlled validation:" in reason
        for reason in result.reasons
    )


def test_policy_readiness_fails_closed_when_authorization_is_stale(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        readiness_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyAudit(
            current=False,
            reasons=("authorization replay mismatch",),
        ),
    )
    monkeypatch.setattr(
        readiness_module,
        "audit_persisted_phase9_policy_controlled_validation",
        lambda storage: DummyAudit(current=True),
    )

    result = evaluate_phase9_policy_readiness(storage)

    assert result.ready is False
    assert result.authorization_current is False
    assert result.controlled_validation_current is True
    assert any(
        "authorization:" in reason
        for reason in result.reasons
    )
