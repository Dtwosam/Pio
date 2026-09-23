import meteora_learner.phase9_policy_status as status_module
from meteora_learner.phase9_policy_status import evaluate_phase9_policy_status
from meteora_learner.storage import Storage


class DummyAudit:
    def __init__(
        self,
        *,
        current,
        rollback_required=None,
        status=None,
        manifest_sha256=None,
    ):
        self.current = current
        self.rollback_required = rollback_required
        self.status = status
        self.manifest_sha256 = manifest_sha256

    def to_record(self):
        return {
            "current": self.current,
            "rollback_required": self.rollback_required,
            "status": self.status,
            "manifest_sha256": self.manifest_sha256,
        }


class DummyPrewire:
    def __init__(self, *, ready):
        self.ready = ready

    def to_record(self):
        return {"ready": self.ready}


def test_phase9_policy_status_consolidates_non_actionable_chain(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyAudit(current=True),
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase9_policy_controlled_validation",
        lambda storage: DummyAudit(current=True),
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase9_policy_rollout_simulation",
        lambda storage: DummyAudit(current=True),
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase9_policy_rollback_simulation",
        lambda storage: DummyAudit(
            current=True,
            rollback_required=False,
            status="NO_ROLLBACK_TRIGGER",
        ),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_policy_prewire_audit",
        lambda storage: DummyPrewire(ready=True),
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase9_policy_prewire_manifest",
        lambda storage: DummyAudit(
            current=True,
            manifest_sha256="a" * 64,
        ),
    )

    result = evaluate_phase9_policy_status(storage)

    assert result.authorization_current is True
    assert result.controlled_validation_current is True
    assert result.rollout_simulation_current is True
    assert result.rollback_simulation_current is True
    assert result.rollback_required is False
    assert result.rollback_status == "NO_ROLLBACK_TRIGGER"
    assert result.prewire_ready is True
    assert result.prewire_manifest_current is True
    assert result.prewire_manifest_sha256 == "a" * 64
    assert result.research_only is True
    assert result.simulation_only is True
    assert result.policy_actionable is False
    assert result.execution_wired is False


def test_phase9_policy_status_reports_stale_chain_without_promoting_it(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyAudit(current=False),
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase9_policy_controlled_validation",
        lambda storage: DummyAudit(current=False),
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase9_policy_rollout_simulation",
        lambda storage: DummyAudit(current=False),
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase9_policy_rollback_simulation",
        lambda storage: DummyAudit(
            current=False,
            rollback_required=True,
            status="ROLLBACK_REQUIRED",
        ),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_policy_prewire_audit",
        lambda storage: DummyPrewire(ready=False),
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase9_policy_prewire_manifest",
        lambda storage: DummyAudit(current=False),
    )

    result = evaluate_phase9_policy_status(storage)

    assert result.authorization_current is False
    assert result.controlled_validation_current is False
    assert result.rollout_simulation_current is False
    assert result.rollback_simulation_current is False
    assert result.rollback_required is True
    assert result.prewire_ready is False
    assert result.prewire_manifest_current is False
    assert result.prewire_manifest_sha256 is None
    assert result.policy_actionable is False
    assert result.execution_wired is False
