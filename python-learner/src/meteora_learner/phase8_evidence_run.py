from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .continuous_learning import ContinuousLearningCriteria
from .phase8_evidence_step import (
    Phase8EvidenceStepReport,
    run_phase8_evidence_step,
)
from .phase8_validation import Phase8PromotionCriteria
from .storage import Storage


@dataclass(frozen=True)
class Phase8EvidenceRunReport:
    research_only: bool
    offline_only: bool
    policy_actionable: bool
    execution_wired: bool
    status: str
    max_steps: int
    steps_attempted: int
    steps_progressed: int
    terminal_debt_type: str | None
    terminal_scope: str | None
    promotion_ready: bool
    persisted_phase8_current: bool
    steps: tuple[Phase8EvidenceStepReport, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def run_phase8_evidence_until_blocked(
    storage: Storage,
    *,
    learning_criteria: ContinuousLearningCriteria = (
        ContinuousLearningCriteria()
    ),
    promotion_criteria: Phase8PromotionCriteria = (
        Phase8PromotionCriteria()
    ),
    max_steps: int = 4,
) -> Phase8EvidenceRunReport:
    if max_steps < 1:
        raise ValueError("max_steps must be positive")

    steps: list[Phase8EvidenceStepReport] = []
    reasons: list[str] = []
    terminal_status = "MAX_STEPS"
    terminal_debt_type: str | None = None
    terminal_scope: str | None = None

    for _ in range(max_steps):
        step = run_phase8_evidence_step(
            storage,
            learning_criteria=learning_criteria,
            promotion_criteria=promotion_criteria,
        )
        steps.append(step)
        terminal_debt_type = step.debt_type
        terminal_scope = step.scope

        if step.status == "READY":
            terminal_status = "READY"
            break
        if step.status in {
            "MANUAL_REQUIRED",
            "WAITING",
            "NOT_QUALIFIED",
            "FAILED",
        }:
            terminal_status = step.status
            if step.error:
                reasons.append(step.error)
            break
        if step.status != "COMPLETE":
            terminal_status = step.status
            reasons.append(
                "unexpected Phase 8 evidence-step status: "
                f"{step.status}"
            )
            break
        if not step.progressed:
            terminal_status = "NO_PROGRESS"
            reasons.append(
                "planner-selected Phase 8 offline step completed without "
                "reducing the current evidence debt"
            )
            break
    else:
        reasons.append(
            f"bounded Phase 8 evidence run reached max_steps={max_steps}"
        )

    final_plan = steps[-1].plan_after if steps else None
    if final_plan is not None:
        reasons.extend(final_plan.reasons)

    return Phase8EvidenceRunReport(
        research_only=True,
        offline_only=True,
        policy_actionable=False,
        execution_wired=False,
        status=terminal_status,
        max_steps=max_steps,
        steps_attempted=len(steps),
        steps_progressed=sum(int(step.progressed) for step in steps),
        terminal_debt_type=terminal_debt_type,
        terminal_scope=terminal_scope,
        promotion_ready=bool(
            final_plan is not None and final_plan.promotion_ready
        ),
        persisted_phase8_current=bool(
            final_plan is not None
            and final_plan.persisted_phase8_current
        ),
        steps=tuple(steps),
        reasons=tuple(dict.fromkeys(reasons)),
    )
