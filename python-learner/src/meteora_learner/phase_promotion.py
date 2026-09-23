from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .storage import Storage


PHASE2 = "PHASE2"
PHASE3 = "PHASE3"
PHASE5 = "PHASE5"
PHASE6 = "PHASE6"
PHASE7 = "PHASE7"
PHASE2_EVIDENCE_TYPE = "PHASE2_PROMOTION_V1"
PHASE3_EVIDENCE_TYPE = "PHASE3_PROMOTION_V1"
PHASE5_EVIDENCE_TYPE = "PHASE5_PROMOTION_V1"
PHASE6_EVIDENCE_TYPE = "PHASE6_PROMOTION_V1"
PHASE7_EVIDENCE_TYPE = "PHASE7_PROMOTION_V1"


@dataclass(frozen=True)
class PhasePromotionState:
    phase_name: str
    promoted: bool
    evidence_type: str


def _record(report: Any) -> dict[str, Any]:
    to_record = getattr(report, "to_record", None)
    if callable(to_record):
        value = to_record()
        if isinstance(value, dict):
            return value
    raise ValueError("promotion report must expose to_record()")


def persist_phase2_promotion(
    storage: Storage,
    *,
    report: Any,
) -> PhasePromotionState:
    if not bool(getattr(report, "promotion_ready", False)):
        raise ValueError("Phase 2 promotion report is not ready")
    storage.save_phase_promotion_evidence(
        phase_name=PHASE2,
        evidence_type=PHASE2_EVIDENCE_TYPE,
        qualified=True,
        evidence=_record(report),
    )
    return phase_promotion_state(storage, phase_name=PHASE2)


def persist_phase3_promotion(
    storage: Storage,
    *,
    report: Any,
) -> PhasePromotionState:
    if not storage.phase_is_promoted(
        PHASE2,
        evidence_type=PHASE2_EVIDENCE_TYPE,
    ):
        raise ValueError("Phase 2 must be persistently promoted before Phase 3")
    if not bool(getattr(report, "promotion_ready", False)):
        raise ValueError("Phase 3 promotion report is not ready")
    storage.save_phase_promotion_evidence(
        phase_name=PHASE3,
        evidence_type=PHASE3_EVIDENCE_TYPE,
        qualified=True,
        evidence=_record(report),
    )
    return phase_promotion_state(storage, phase_name=PHASE3)


def persist_phase5_promotion(
    storage: Storage,
    *,
    report: Any,
) -> PhasePromotionState:
    if not storage.phase_is_promoted(
        PHASE3,
        evidence_type=PHASE3_EVIDENCE_TYPE,
    ):
        raise ValueError("Phase 3 must be persistently promoted before Phase 5")
    if not bool(getattr(report, "promotion_ready", False)):
        raise ValueError("Phase 5 promotion report is not ready")
    storage.save_phase_promotion_evidence(
        phase_name=PHASE5,
        evidence_type=PHASE5_EVIDENCE_TYPE,
        qualified=True,
        evidence=_record(report),
    )
    return phase_promotion_state(storage, phase_name=PHASE5)


def persist_phase6_promotion(
    storage: Storage,
    *,
    report: Any,
) -> PhasePromotionState:
    if not storage.phase_is_promoted(
        PHASE5,
        evidence_type=PHASE5_EVIDENCE_TYPE,
    ):
        raise ValueError("Phase 5 must be persistently promoted before Phase 6")
    if not bool(getattr(report, "promotion_ready", False)):
        raise ValueError("Phase 6 promotion report is not ready")
    storage.save_phase_promotion_evidence(
        phase_name=PHASE6,
        evidence_type=PHASE6_EVIDENCE_TYPE,
        qualified=True,
        evidence=_record(report),
    )
    return phase_promotion_state(storage, phase_name=PHASE6)


def persist_phase7_promotion(
    storage: Storage,
    *,
    report: Any,
) -> PhasePromotionState:
    if not storage.phase_is_promoted(
        PHASE6,
        evidence_type=PHASE6_EVIDENCE_TYPE,
    ):
        raise ValueError("Phase 6 must be persistently promoted before Phase 7")
    if not bool(getattr(report, "promotion_ready", False)):
        raise ValueError("Phase 7 promotion report is not ready")
    storage.save_phase_promotion_evidence(
        phase_name=PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
        qualified=True,
        evidence=_record(report),
    )
    return phase_promotion_state(storage, phase_name=PHASE7)


def phase_promotion_state(
    storage: Storage,
    *,
    phase_name: str,
) -> PhasePromotionState:
    if phase_name == PHASE2:
        evidence_type = PHASE2_EVIDENCE_TYPE
    elif phase_name == PHASE3:
        evidence_type = PHASE3_EVIDENCE_TYPE
    elif phase_name == PHASE5:
        evidence_type = PHASE5_EVIDENCE_TYPE
    elif phase_name == PHASE6:
        evidence_type = PHASE6_EVIDENCE_TYPE
    elif phase_name == PHASE7:
        evidence_type = PHASE7_EVIDENCE_TYPE
    else:
        raise ValueError(f"unsupported phase_name: {phase_name}")
    return PhasePromotionState(
        phase_name=phase_name,
        promoted=storage.phase_is_promoted(
            phase_name,
            evidence_type=evidence_type,
        ),
        evidence_type=evidence_type,
    )
