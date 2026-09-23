import meteora_learner.phase9_policy_prewire as prewire_module
from meteora_learner.phase9_policy_prewire import (
    evaluate_phase9_policy_prewire_audit,
)
from meteora_learner.storage import Storage


class DummyReadiness:
    def __init__(self, *, ready=True, reasons=()):
        self.ready = ready
        self.reasons = tuple(reasons)

    def to_record(self):
        return {"ready": self.ready, "reasons": list(self.reasons)}


class DummyAudit:
    def __init__(
        self,
        *,
        current=True,
        reasons=(),
        rollback_required=None,
        status=None,
    ):
        self.current = current
        self.reasons = tuple(reasons)
        self.rollback_required = rollback_required
        self.status = status

    def to_record(self):
        return {
            "current": self.current,
            "reasons": list(self.reasons),
            "rollback_required": self.rollback_required,
            "status": self.status,
        }


def patch_all(
    monkeypatch,
    *,
    readiness=None,
    rollout=None,
    rollback=None,
):
    readiness = readiness or DummyReadiness()
    rollout = rollout or DummyAudit()
    rollback = rollback or DummyAudit(
        rollback_required=False,
        status="NO_ROLLBACK_TRIGGER",
    )
    monkeypatch.setattr(
        prewire_module,
        "evaluate_phase9_policy_readiness",
        lambda storage: readiness,
    )
    monkeypatch.setattr(
        prewire_module,
        "audit_persisted_phase9_policy_rollout_simulation",
        lambda storage: rollout,
    )
    monkeypatch.setattr(
        prewire_module,
        "audit_persisted_phase9_policy_rollback_simulation",
        lambda storage: rollback,
    )


def test_prewire_ready_when_all_non_actionable_evidence_is_current(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_all(monkeypatch)

    result = evaluate_phase9_policy_prewire_audit(storage)

    assert result.ready is True
    assert result.rollback_clear is True
    assert result.research_only is True
    assert result.simulation_only is True
    assert result.policy_actionable is False
    assert result.execution_wired is False
    assert result.reasons == ()


def test_prewire_fails_when_rollback_is_observation_pending(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_all(
        monkeypatch,
        rollback=DummyAudit(
            current=True,
            rollback_required=False,
            status="OBSERVATION_PENDING",
        ),
    )

    result = evaluate_phase9_policy_prewire_audit(storage)

    assert result.ready is False
    assert result.rollback_clear is False
    assert any(
        "has not resolved to NO_ROLLBACK_TRIGGER" in reason
        for reason in result.reasons
    )


def test_prewire_fails_when_rollout_or_policy_readiness_is_stale(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_all(
        monkeypatch,
        readiness=DummyReadiness(
            ready=False,
            reasons=("authorization is stale",),
        ),
        rollout=DummyAudit(
            current=False,
            reasons=("rollout evidence is stale",),
        ),
    )

    result = evaluate_phase9_policy_prewire_audit(storage)

    assert result.ready is False
    assert result.policy_readiness_ready is False
    assert result.rollout_simulation_current is False
    assert any(
        reason.startswith("policy readiness:")
        for reason in result.reasons
    )
    assert any(
        reason.startswith("rollout simulation:")
        for reason in result.reasons
    )


def test_prewire_fails_when_current_rollback_requires_rollback(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_all(
        monkeypatch,
        rollback=DummyAudit(
            current=True,
            rollback_required=True,
            status="ROLLBACK_REQUIRED",
        ),
    )

    result = evaluate_phase9_policy_prewire_audit(storage)

    assert result.ready is False
    assert result.rollback_clear is False


class DummyStorageIntegrity:
    def __init__(self, *, verified, reasons=()):
        self.verified = verified
        self.reasons = tuple(reasons)

    def to_record(self):
        return {
            "verified": self.verified,
            "reasons": list(self.reasons),
        }


def test_prewire_fails_when_phase9_storage_integrity_breaks(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_all(monkeypatch)
    monkeypatch.setattr(
        prewire_module,
        "evaluate_phase9_storage_integrity",
        lambda storage: DummyStorageIntegrity(
            verified=False,
            reasons=("advanced edge evidence update trigger is missing",),
        ),
    )

    result = evaluate_phase9_policy_prewire_audit(storage)

    assert result.ready is False
    assert result.storage_integrity_verified is False
    assert any(
        reason.startswith("storage integrity:")
        for reason in result.reasons
    )
