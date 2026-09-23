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




def phase9_report(
    *,
    ready=True,
    current_sha="a" * 64,
    persisted_sha="a" * 64,
    bundle_id=7,
    hash_valid=True,
    matches_current=True,
):
    bundle = SimpleNamespace(
        research_ready=True,
        policy_actionable=False,
        research_only=True,
    )
    return SimpleNamespace(
        promotion_ready=ready,
        policy_actionable=False,
        research_only=True,
        research_bundle=bundle,
        research_bundle_evidence_id=bundle_id,
        research_bundle_sha256=current_sha,
        persisted_bundle_sha256=persisted_sha,
        persisted_bundle_hash_valid=hash_valid,
        persisted_bundle_matches_current=matches_current,
        to_record=lambda: {
            "promotion_ready": ready,
            "policy_actionable": False,
            "research_only": True,
            "research_bundle_evidence_id": bundle_id,
            "research_bundle_sha256": current_sha,
            "persisted_bundle_sha256": persisted_sha,
            "persisted_bundle_hash_valid": hash_valid,
            "persisted_bundle_matches_current": matches_current,
        },
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



def test_phase9_promotion_requires_bundle_provenance(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )

    with pytest.raises(ValueError, match="persisted research-bundle evidence"):
        persist_phase9_promotion(
            storage,
            report=phase9_report(bundle_id=None),
        )


def test_phase9_promotion_rejects_stale_bundle_checksum(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )

    with pytest.raises(ValueError, match="checksum is not current"):
        persist_phase9_promotion(
            storage,
            report=phase9_report(
                current_sha="a" * 64,
                persisted_sha="b" * 64,
            ),
        )


def test_phase9_promotion_rejects_unverified_bundle_hash(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )

    with pytest.raises(ValueError, match="valid persisted bundle checksum"):
        persist_phase9_promotion(
            storage,
            report=phase9_report(hash_valid=False),
        )


def test_phase9_promotion_accepts_current_non_actionable_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )

    state = persist_phase9_promotion(
        storage,
        report=phase9_report(),
    )

    assert state.phase_name == "PHASE9"
    assert state.promoted is True
