from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from typing import Any, Sequence

from .paper_action_replay import (
    PAPER_HOLD_VS_REBALANCE_EVIDENCE_TYPE,
    PaperHoldVsRebalanceReplayReport,
)
from .storage import Storage


PAPER_MANAGEMENT_DATASET_EVIDENCE_TYPE = (
    "PAPER_MANAGEMENT_DATASET_GATE_V1"
)


@dataclass(frozen=True)
class PaperManagementObservation:
    pool_address: str
    decision_observed_at: str
    forward_end_observed_at: str
    strategy: str
    min_bin_id: int
    max_bin_id: int
    range_width_bins: int
    start_x: int
    start_y: int
    start_value_y_atomic: int
    quote_unit: str
    quote_normalization_complete: bool
    economics_complete: bool
    transition_cost_complete: bool
    reward_value_complete: bool
    gross_advantage_before_transition_cost_quote: float | None
    incomplete_economic_components: tuple[str, ...]
    training_eligible: bool
    training_exclusion_reason: str | None
    target_net_advantage_after_all_costs_quote: float | None
    target_action: str | None


@dataclass(frozen=True)
class PaperManagementTrainingExample:
    pool_address: str
    decision_observed_at: str
    forward_end_observed_at: str
    strategy: str
    min_bin_id: int
    max_bin_id: int
    range_width_bins: int
    start_x: int
    start_y: int
    start_value_y_atomic: int
    target_net_advantage_after_all_costs_quote: float
    target_rebalance_wins: int


@dataclass(frozen=True)
class PaperManagementDatasetReport:
    observations_seen: int
    observations_recorded: int
    training_examples_built: int
    training_examples_dropped: int
    drop_reasons: tuple[tuple[str, int], ...]
    paper_only: bool
    live_authorized: bool
    observations: tuple[PaperManagementObservation, ...]
    training_examples: tuple[PaperManagementTrainingExample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("management dataset timestamps require timezone")
    return parsed.astimezone(timezone.utc)


def _bump(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def build_paper_management_dataset(
    reports: Sequence[PaperHoldVsRebalanceReplayReport],
) -> PaperManagementDatasetReport:
    """
    Build PAPER management observations without leaking incomplete outcomes
    into training labels.

    Decision-time features are limited to range/strategy/start inventory fields.
    Gross forward advantage remains diagnostic only. A training label requires:
    - PAPER-only / non-live evidence;
    - quote normalization complete;
    - reward valuation complete;
    - transition cost complete;
    - economics complete;
    - an explicit quote-normalized net advantage after all costs.

    Current gross replay reports intentionally fail these requirements.
    """
    if not reports:
        raise ValueError("at least one management report is required")

    observations: list[PaperManagementObservation] = []
    examples: list[PaperManagementTrainingExample] = []
    drops: dict[str, int] = {}

    for report in reports:
        if not report.paper_only or report.live_authorized or report.actionable:
            raise ValueError(
                "management dataset accepts PAPER-only non-actionable evidence"
            )
        decision_time = _parse_time(report.decision_observed_at)
        end_time = _parse_time(report.end_observed_at)
        if end_time <= decision_time:
            raise ValueError(
                "management outcome must end after the decision timestamp"
            )
        if report.min_bin_id > report.max_bin_id:
            raise ValueError("management report range is inverted")

        final_net_raw = getattr(
            report,
            "net_advantage_after_all_costs_quote",
            None,
        )
        final_net = (
            float(final_net_raw)
            if final_net_raw is not None
            else None
        )

        exclusion = None
        quote_complete = bool(
            getattr(report, "quote_normalization_complete", False)
        )
        reward_complete = bool(
            getattr(report, "reward_value_complete", False)
        )
        transition_complete = bool(
            getattr(report, "transition_cost_complete", False)
        )
        economics_complete = bool(
            getattr(report, "economics_complete", False)
        )

        if not quote_complete:
            exclusion = "QUOTE_NORMALIZATION_INCOMPLETE"
        elif not reward_complete:
            exclusion = "REWARD_VALUATION_INCOMPLETE"
        elif not transition_complete:
            exclusion = "TRANSITION_COST_INCOMPLETE"
        elif not economics_complete:
            exclusion = "ECONOMICS_INCOMPLETE"
        elif final_net is None:
            exclusion = "FINAL_NET_ADVANTAGE_MISSING"
        elif final_net == 0.0:
            exclusion = "REALIZED_ACTION_TIE"

        target_action = None
        if exclusion is None:
            target_action = "REBALANCE" if final_net > 0.0 else "HOLD"

        observation = PaperManagementObservation(
            pool_address=report.pool_address,
            decision_observed_at=report.decision_observed_at,
            forward_end_observed_at=report.end_observed_at,
            strategy=report.strategy,
            min_bin_id=report.min_bin_id,
            max_bin_id=report.max_bin_id,
            range_width_bins=(
                report.max_bin_id - report.min_bin_id + 1
            ),
            start_x=report.start_x,
            start_y=report.start_y,
            start_value_y_atomic=report.start_value_y_atomic,
            quote_unit=str(getattr(report, "quote_unit", "UNKNOWN")),
            quote_normalization_complete=quote_complete,
            economics_complete=economics_complete,
            transition_cost_complete=transition_complete,
            reward_value_complete=reward_complete,
            gross_advantage_before_transition_cost_quote=(
                float(getattr(
                    report,
                    "gross_advantage_before_transition_cost_quote",
                    0.0,
                ))
                if getattr(
                    report,
                    "gross_advantage_before_transition_cost_quote",
                    None,
                ) is not None
                else None
            ),
            incomplete_economic_components=tuple(
                getattr(report, "incomplete_economic_components", ())
            ),
            training_eligible=exclusion is None,
            training_exclusion_reason=exclusion,
            target_net_advantage_after_all_costs_quote=final_net,
            target_action=target_action,
        )
        observations.append(observation)

        if exclusion is not None:
            _bump(drops, exclusion)
            continue

        assert final_net is not None
        examples.append(
            PaperManagementTrainingExample(
                pool_address=report.pool_address,
                decision_observed_at=report.decision_observed_at,
                forward_end_observed_at=report.end_observed_at,
                strategy=report.strategy,
                min_bin_id=report.min_bin_id,
                max_bin_id=report.max_bin_id,
                range_width_bins=(
                    report.max_bin_id - report.min_bin_id + 1
                ),
                start_x=report.start_x,
                start_y=report.start_y,
                start_value_y_atomic=report.start_value_y_atomic,
                target_net_advantage_after_all_costs_quote=final_net,
                target_rebalance_wins=int(final_net > 0.0),
            )
        )

    return PaperManagementDatasetReport(
        observations_seen=len(reports),
        observations_recorded=len(observations),
        training_examples_built=len(examples),
        training_examples_dropped=len(reports) - len(examples),
        drop_reasons=tuple(sorted(drops.items())),
        paper_only=True,
        live_authorized=False,
        observations=tuple(observations),
        training_examples=tuple(examples),
    )


def persist_paper_management_dataset(
    storage: Storage,
    *,
    pool_address: str,
    report: PaperManagementDatasetReport,
) -> int:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if not report.paper_only or report.live_authorized:
        raise ValueError("management dataset crossed PAPER-only boundary")
    if any(
        item.pool_address != pool_address
        for item in report.observations
    ):
        raise ValueError("management dataset contains another pool")
    return storage.save_advanced_edge_evidence(
        edge_type=PAPER_MANAGEMENT_DATASET_EVIDENCE_TYPE,
        pool_address=pool_address,
        status=(
            "TRAINING_EXAMPLES_AVAILABLE"
            if report.training_examples_built
            else "LABELS_BLOCKED_INCOMPLETE_ECONOMICS"
        ),
        qualified=False,
        evidence=report.to_record(),
    )



def load_persisted_paper_management_reports(
    storage: Storage,
    *,
    pool_address: str,
) -> tuple[Any, ...]:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT evidence_json
            FROM advanced_edge_evidence
            WHERE edge_type = ?
              AND pool_address = ?
            ORDER BY id ASC
            """,
            (
                PAPER_HOLD_VS_REBALANCE_EVIDENCE_TYPE,
                pool_address,
            ),
        ).fetchall()

    reports: list[Any] = []
    for row in rows:
        try:
            payload = json.loads(str(row[0]))
        except json.JSONDecodeError as exc:
            raise ValueError(
                "persisted PAPER management evidence is invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                "persisted PAPER management evidence must be an object"
            )
        reports.append(SimpleNamespace(**payload))
    return tuple(reports)


def build_persisted_paper_management_dataset(
    storage: Storage,
    *,
    pool_address: str,
) -> PaperManagementDatasetReport:
    reports = load_persisted_paper_management_reports(
        storage,
        pool_address=pool_address,
    )
    if not reports:
        raise ValueError(
            f"no persisted PAPER HOLD-vs-REBALANCE evidence for {pool_address}"
        )
    return build_paper_management_dataset(reports)
