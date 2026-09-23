from types import SimpleNamespace

import pytest

from meteora_learner.phase_promotion import (
    PHASE2,
    PHASE3,
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
    persist_phase2_promotion,
    persist_phase3_promotion,
    persist_phase9_promotion,
    phase_promotion_state,
)
from meteora_learner.storage import Storage


def report(*, ready):
    return SimpleNamespace(
        promotion_ready=ready,
        to_record=lambda: {"promotion_ready": ready},
    )


def test_phase3_persistence_requires_phase2_persistence(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="Phase 2"):
        persist_phase3_promotion(storage, report=report(ready=True))

    persist_phase2_promotion(storage, report=report(ready=True))
    persist_phase3_promotion(storage, report=report(ready=True))

    assert phase_promotion_state(
        storage,
        phase_name=PHASE2,
    ).promoted is True
    assert phase_promotion_state(
        storage,
        phase_name=PHASE3,
    ).promoted is True


def test_unready_report_cannot_be_persisted(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="not ready"):
        persist_phase2_promotion(storage, report=report(ready=False))


def test_phase9_promotion_rejects_actionable_report(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )
    actionable = SimpleNamespace(
        promotion_ready=True,
        policy_actionable=True,
        research_only=True,
        to_record=lambda: {
            "promotion_ready": True,
            "policy_actionable": True,
            "research_only": True,
        },
    )

    with pytest.raises(ValueError, match="live-policy authority"):
        persist_phase9_promotion(
            storage,
            report=actionable,
        )
