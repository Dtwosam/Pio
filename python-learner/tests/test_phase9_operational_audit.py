from dataclasses import dataclass

from meteora_learner.phase9_operational_audit import (
    evaluate_phase9_operational_audit,
)
from meteora_learner.storage import Storage


@dataclass(frozen=True)
class DummyFreshness:
    current: bool = True

    def to_record(self):
        return {
            "current": self.current,
            "families": [],
        }


@dataclass(frozen=True)
class DummyReport:
    verified: bool = True
    current: bool = True
    reasons: tuple[str, ...] = ()

    def to_record(self):
        return {
            "verified": self.verified,
            "current": self.current,
            "reasons": list(self.reasons),
        }


def test_phase9_operational_audit_requires_all_components(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")

    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "evaluate_phase9_storage_integrity",
        lambda storage: DummyReport(verified=True),
    )
    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "evaluate_phase9_replay_audit",
        lambda storage, criteria: DummyReport(verified=True),
    )
    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "evaluate_phase9_promotion",
        lambda storage, criteria: object(),
    )
    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "audit_persisted_phase9_promotion",
        lambda storage, criteria, current_report: DummyReport(
            current=True
        ),
    )

    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "evaluate_phase9_source_freshness",
        lambda storage: DummyFreshness(current=False),
    )

    report = evaluate_phase9_operational_audit(storage)

    assert report.verified is True
    assert report.storage_integrity_verified is True
    assert report.replay_verified is True
    assert report.promotion_current is True
    assert report.source_freshness_current is False
    assert report.verified is True
    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.reasons == ()


def test_phase9_operational_audit_fails_on_replay_mismatch(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")

    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "evaluate_phase9_storage_integrity",
        lambda storage: DummyReport(verified=True),
    )
    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "evaluate_phase9_replay_audit",
        lambda storage, criteria: DummyReport(
            verified=False,
            reasons=("wallet_flow: replay mismatch",),
        ),
    )
    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "evaluate_phase9_promotion",
        lambda storage, criteria: object(),
    )
    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "audit_persisted_phase9_promotion",
        lambda storage, criteria, current_report: DummyReport(
            current=True
        ),
    )

    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "evaluate_phase9_source_freshness",
        lambda storage: DummyFreshness(current=False),
    )

    report = evaluate_phase9_operational_audit(storage)

    assert report.verified is False
    assert report.storage_integrity_verified is True
    assert report.replay_verified is False
    assert report.promotion_current is True
    assert report.source_freshness_current is False
    assert any(
        "replay audit: wallet_flow: replay mismatch" == reason
        for reason in report.reasons
    )


def test_phase9_operational_audit_exposes_source_freshness_without_changing_verification(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")

    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "evaluate_phase9_storage_integrity",
        lambda storage: DummyReport(verified=True),
    )
    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "evaluate_phase9_replay_audit",
        lambda storage, criteria: DummyReport(verified=True),
    )
    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "evaluate_phase9_promotion",
        lambda storage, criteria: object(),
    )
    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "audit_persisted_phase9_promotion",
        lambda storage, criteria, current_report: DummyReport(
            current=True
        ),
    )
    monkeypatch.setattr(
        "meteora_learner.phase9_operational_audit."
        "evaluate_phase9_source_freshness",
        lambda storage: DummyFreshness(current=False),
    )

    report = evaluate_phase9_operational_audit(storage)

    assert report.verified is True
    assert report.source_freshness_current is False
    assert report.source_freshness["current"] is False
    assert report.reasons == ()
