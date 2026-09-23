from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .baseline_policy import BaselineProposal, BaselineSelection
from .capital_sizing import CapitalSizingResult
from .pool_safety import PoolSafetyAssessment


@dataclass(frozen=True)
class Phase3EntryGate:
    pool_address: str
    phase2_ready: bool
    pool_safe: bool
    baseline_selected: bool
    sizing_ready: bool
    research_ready: bool
    entry_authorized: bool
    proposal: BaselineProposal | None
    sized_quote: float
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase3_entry_gate(
    *,
    pool_safety: PoolSafetyAssessment,
    baseline: BaselineSelection,
    sizing: CapitalSizingResult,
) -> Phase3EntryGate:
    if pool_safety.pool_address != baseline.pool_address:
        raise ValueError("pool safety and baseline refer to different pools")

    reasons: list[str] = []
    phase2_ready = baseline.phase2_ready
    pool_safe = pool_safety.accepted
    proposal = baseline.research_proposal
    baseline_selected = proposal is not None
    sizing_ready = not sizing.blocked and sizing.sized_quote > 0
    if (
        sizing_ready
        and sizing.requested_quote is not None
        and sizing.sized_quote + 1e-12 < sizing.requested_quote
    ):
        sizing_ready = False

    if not phase2_ready:
        reasons.extend(baseline.phase2_blockers or ("Phase 2 is not promoted",))
    if not pool_safe:
        reasons.extend(
            f"pool safety: {reason}"
            for reason in pool_safety.rejection_reasons
        )
    if not baseline_selected:
        reasons.append("no deterministic baseline candidate passed")
    if not sizing_ready:
        reasons.extend(
            f"capital sizing: {reason}"
            for reason in sizing.reasons
        )
        if (
            sizing.requested_quote is not None
            and sizing.sized_quote > 0
            and sizing.sized_quote < sizing.requested_quote
        ):
            reasons.append(
                "capital sizing: requested notional exceeds allowed size; "
                "atomic proposal must be rescaled"
            )
        if not sizing.reasons and sizing.sized_quote <= 0:
            reasons.append("capital sizing produced no deployable capital")

    research_ready = pool_safe and baseline_selected and sizing_ready
    entry_authorized = research_ready and phase2_ready

    return Phase3EntryGate(
        pool_address=baseline.pool_address,
        phase2_ready=phase2_ready,
        pool_safe=pool_safe,
        baseline_selected=baseline_selected,
        sizing_ready=sizing_ready,
        research_ready=research_ready,
        entry_authorized=entry_authorized,
        proposal=proposal if research_ready else None,
        sized_quote=sizing.sized_quote if research_ready else 0.0,
        reasons=tuple(dict.fromkeys(reasons)),
    )
