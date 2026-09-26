from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from .pool_api_metadata_ablation import (
    APIMetadataAblationReport,
    APIMetadataTrainingFrameReport,
    build_api_metadata_enriched_lp_frame,
    evaluate_api_metadata_ablation,
)
from .pool_lp_context_ablation import (
    ContextFeatureAblationReport,
    evaluate_context_feature_ablation,
)
from .pool_lp_cross_sectional_ranking import (
    CrossSectionRankingReport,
    evaluate_cross_sectional_ranking,
)
from .pool_execution_cost_context import (
    ExecutionCostAblationReport,
    ExecutionCostTrainingFrameReport,
    build_execution_cost_enriched_lp_frame,
    evaluate_execution_cost_ablation,
)
from .pool_lp_feature_drift import (
    FeatureDriftReport,
    evaluate_feature_drift,
)
from .pool_lp_learning import (
    EnrichedLPTrainingFrameReport,
    build_market_enriched_lp_training_frame,
)
from .pool_lp_mint_ablation import (
    MintEnrichedLPTrainingFrameReport,
    MintFeatureAblationReport,
    build_mint_enriched_lp_training_frame,
    evaluate_mint_feature_ablation,
)
from .pool_lp_outcome_coverage import (
    OutcomeCoverageComparison,
    OutcomeCoverageReport,
    build_outcome_coverage_report,
    compare_outcome_coverage,
)
from .pool_lp_rotation_evidence import (
    RotationEvidenceReport,
    evaluate_rotation_transition_evidence,
)
from .pool_lp_tail_risk import (
    TailRiskCalibrationReport,
    evaluate_tail_risk_calibration,
)
from .pool_lp_unseen_pool import (
    UnseenPoolValidationReport,
    evaluate_unseen_pool_walk_forward,
)
from .pool_market_learning import load_pool_market_history


@dataclass(frozen=True)
class MintFeatureResearchReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    status: str
    reason: str | None
    source_dataset_rows: int
    source_dataset_sha256: str
    market_enrichment: EnrichedLPTrainingFrameReport | None
    mint_enrichment: MintEnrichedLPTrainingFrameReport | None
    ablation: MintFeatureAblationReport | None
    context_ablation: ContextFeatureAblationReport | None = None
    unseen_pool_validation: UnseenPoolValidationReport | None = None
    unseen_pool_reason: str | None = None
    tail_risk_calibration: TailRiskCalibrationReport | None = None
    tail_risk_reason: str | None = None
    feature_drift: FeatureDriftReport | None = None
    feature_drift_reason: str | None = None
    source_outcome_coverage: OutcomeCoverageReport | None = None
    model_ready_outcome_coverage: OutcomeCoverageComparison | None = None
    api_metadata_enrichment: APIMetadataTrainingFrameReport | None = None
    api_metadata_ablation: APIMetadataAblationReport | None = None
    api_metadata_reason: str | None = None
    execution_cost_enrichment: ExecutionCostTrainingFrameReport | None = None
    execution_cost_ablation: ExecutionCostAblationReport | None = None
    execution_cost_reason: str | None = None
    cross_sectional_ranking: CrossSectionRankingReport | None = None
    cross_sectional_ranking_reason: str | None = None
    rotation_evidence: RotationEvidenceReport | None = None
    rotation_evidence_reason: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "research_only": self.research_only,
            "policy_actionable": self.policy_actionable,
            "execution_wired": self.execution_wired,
            "status": self.status,
            "reason": self.reason,
            "source_dataset_rows": self.source_dataset_rows,
            "source_dataset_sha256": self.source_dataset_sha256,
            "market_enrichment": (
                self.market_enrichment.to_record()
                if self.market_enrichment is not None
                else None
            ),
            "mint_enrichment": (
                self.mint_enrichment.to_record()
                if self.mint_enrichment is not None
                else None
            ),
            "ablation": (
                self.ablation.to_record()
                if self.ablation is not None
                else None
            ),
            "context_ablation": (
                self.context_ablation.to_record()
                if self.context_ablation is not None
                else None
            ),
            "unseen_pool_validation": (
                self.unseen_pool_validation.to_record()
                if self.unseen_pool_validation is not None
                else None
            ),
            "unseen_pool_reason": self.unseen_pool_reason,
            "tail_risk_calibration": (
                self.tail_risk_calibration.to_record()
                if self.tail_risk_calibration is not None
                else None
            ),
            "tail_risk_reason": self.tail_risk_reason,
            "feature_drift": (
                self.feature_drift.to_record()
                if self.feature_drift is not None
                else None
            ),
            "feature_drift_reason": self.feature_drift_reason,
            "source_outcome_coverage": (
                self.source_outcome_coverage.to_record()
                if self.source_outcome_coverage is not None
                else None
            ),
            "model_ready_outcome_coverage": (
                self.model_ready_outcome_coverage.to_record()
                if self.model_ready_outcome_coverage is not None
                else None
            ),
            "api_metadata_enrichment": (
                self.api_metadata_enrichment.to_record()
                if self.api_metadata_enrichment is not None
                else None
            ),
            "api_metadata_ablation": (
                self.api_metadata_ablation.to_record()
                if self.api_metadata_ablation is not None
                else None
            ),
            "api_metadata_reason": self.api_metadata_reason,
            "execution_cost_enrichment": (
                self.execution_cost_enrichment.to_record()
                if self.execution_cost_enrichment is not None
                else None
            ),
            "execution_cost_ablation": (
                self.execution_cost_ablation.to_record()
                if self.execution_cost_ablation is not None
                else None
            ),
            "execution_cost_reason": self.execution_cost_reason,
            "cross_sectional_ranking": (
                self.cross_sectional_ranking.to_record()
                if self.cross_sectional_ranking is not None
                else None
            ),
            "cross_sectional_ranking_reason": (
                self.cross_sectional_ranking_reason
            ),
            "rotation_evidence": (
                self.rotation_evidence.to_record()
                if self.rotation_evidence is not None
                else None
            ),
            "rotation_evidence_reason": self.rotation_evidence_reason,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_mint_feature_research_from_dataset_file(
    database_path: str,
    dataset_file: str | Path,
    *,
    volatility_window: int = 6,
    drawdown_window: int = 12,
    activity_window: int = 6,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> MintFeatureResearchReport:
    """
    Evaluate token/mint context on Pio's existing canonical LP dataset.

    The source dataset is not relabeled. Market and mint state are attached
    strictly as-of each existing decision timestamp. The result is descriptive
    research evidence only.
    """
    path = Path(dataset_file)
    if not path.is_file():
        raise ValueError(f"dataset file does not exist: {path}")

    source_sha = _sha256(path)
    source = pd.read_csv(path)
    if source.empty:
        return MintFeatureResearchReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            status="COLLECTING_CONTEXT",
            reason="canonical LP dataset is empty",
            source_dataset_rows=0,
            source_dataset_sha256=source_sha,
            market_enrichment=None,
            mint_enrichment=None,
            ablation=None,
        )

    source_outcome_coverage = build_outcome_coverage_report(
        source
    )

    required = {
        "pool_address",
        "decision_observed_at",
        "forward_end_observed_at",
    }
    missing = sorted(required - set(source.columns))
    if missing:
        raise ValueError(
            f"canonical LP dataset is missing columns: {missing}"
        )

    pool_addresses = tuple(
        sorted(
            {
                str(value).strip()
                for value in source["pool_address"]
                if str(value).strip()
            }
        )
    )
    if not pool_addresses:
        raise ValueError(
            "canonical LP dataset contains no pool addresses"
        )

    market_history = load_pool_market_history(
        database_path,
        pool_addresses=pool_addresses,
    )

    try:
        market_frame, market_report = (
            build_market_enriched_lp_training_frame(
                source,
                market_history,
                volatility_window=volatility_window,
                drawdown_window=drawdown_window,
                activity_window=activity_window,
            )
        )
    except ValueError as exc:
        return MintFeatureResearchReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            status="COLLECTING_CONTEXT",
            reason=str(exc),
            source_dataset_rows=len(source),
            source_dataset_sha256=source_sha,
            market_enrichment=None,
            mint_enrichment=None,
            ablation=None,
            source_outcome_coverage=source_outcome_coverage,
        )

    if market_frame.empty:
        return MintFeatureResearchReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            status="COLLECTING_CONTEXT",
            reason=(
                "no canonical LP rows have complete decision-time "
                "market context"
            ),
            source_dataset_rows=len(source),
            source_dataset_sha256=source_sha,
            market_enrichment=market_report,
            mint_enrichment=None,
            ablation=None,
            source_outcome_coverage=source_outcome_coverage,
        )

    mint_frame, mint_report = build_mint_enriched_lp_training_frame(
        database_path,
        market_frame,
    )
    model_ready_outcome_coverage = compare_outcome_coverage(
        source,
        mint_frame,
    )
    if mint_frame.empty:
        return MintFeatureResearchReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            status="COLLECTING_CONTEXT",
            reason=(
                "no market-enriched LP rows have complete decision-time "
                "token/mint context"
            ),
            source_dataset_rows=len(source),
            source_dataset_sha256=source_sha,
            market_enrichment=market_report,
            mint_enrichment=mint_report,
            ablation=None,
            source_outcome_coverage=source_outcome_coverage,
            model_ready_outcome_coverage=model_ready_outcome_coverage,
        )

    try:
        ablation = evaluate_mint_feature_ablation(
            mint_frame,
            min_train_decision_times=min_train_decision_times,
            validation_decision_times=validation_decision_times,
            step_decision_times=step_decision_times,
            min_train_rows=min_train_rows,
        )
        context_ablation = evaluate_context_feature_ablation(
            mint_frame,
            min_train_decision_times=min_train_decision_times,
            validation_decision_times=validation_decision_times,
            step_decision_times=step_decision_times,
            min_train_rows=min_train_rows,
        )
    except ValueError as exc:
        return MintFeatureResearchReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            status="COLLECTING_CONTEXT",
            reason=str(exc),
            source_dataset_rows=len(source),
            source_dataset_sha256=source_sha,
            market_enrichment=market_report,
            mint_enrichment=mint_report,
            ablation=None,
            context_ablation=None,
            source_outcome_coverage=source_outcome_coverage,
            model_ready_outcome_coverage=model_ready_outcome_coverage,
        )

    unseen_pool_validation = None
    unseen_pool_reason = None
    try:
        unseen_pool_validation = evaluate_unseen_pool_walk_forward(
            mint_frame,
            min_train_decision_times=min_train_decision_times,
            validation_decision_times=validation_decision_times,
            step_decision_times=step_decision_times,
            min_train_rows=min_train_rows,
        )
    except ValueError as exc:
        unseen_pool_reason = str(exc)

    tail_risk_calibration = None
    tail_risk_reason = None
    try:
        tail_risk_calibration = evaluate_tail_risk_calibration(
            mint_frame,
            min_train_decision_times=min_train_decision_times,
            validation_decision_times=validation_decision_times,
            step_decision_times=step_decision_times,
            min_train_rows=min_train_rows,
        )
    except ValueError as exc:
        tail_risk_reason = str(exc)

    feature_drift = None
    feature_drift_reason = None
    try:
        feature_drift = evaluate_feature_drift(
            mint_frame,
            min_train_decision_times=min_train_decision_times,
            validation_decision_times=validation_decision_times,
            step_decision_times=step_decision_times,
            min_train_rows=min_train_rows,
        )
    except ValueError as exc:
        feature_drift_reason = str(exc)

    api_metadata_enrichment = None
    api_metadata_ablation = None
    api_metadata_reason = None
    try:
        metadata_frame, api_metadata_enrichment = (
            build_api_metadata_enriched_lp_frame(
                database_path,
                mint_frame,
            )
        )
        if metadata_frame.empty:
            api_metadata_reason = (
                "no mint-enriched LP rows have complete decision-time "
                "pool API metadata"
            )
        else:
            api_metadata_ablation = evaluate_api_metadata_ablation(
                metadata_frame,
                min_train_decision_times=min_train_decision_times,
                validation_decision_times=validation_decision_times,
                step_decision_times=step_decision_times,
                min_train_rows=min_train_rows,
            )
    except ValueError as exc:
        api_metadata_reason = str(exc)

    execution_cost_enrichment = None
    execution_cost_ablation = None
    execution_cost_reason = None
    try:
        execution_frame, execution_cost_enrichment = (
            build_execution_cost_enriched_lp_frame(
                database_path,
                mint_frame,
            )
        )
        if execution_frame.empty:
            execution_cost_reason = (
                "no mint-enriched LP rows have complete prior "
                "execution-cost history"
            )
        else:
            execution_cost_ablation = evaluate_execution_cost_ablation(
                execution_frame,
                min_train_decision_times=min_train_decision_times,
                validation_decision_times=validation_decision_times,
                step_decision_times=step_decision_times,
                min_train_rows=min_train_rows,
            )
    except ValueError as exc:
        execution_cost_reason = str(exc)

    cross_sectional_ranking = None
    cross_sectional_ranking_reason = None
    try:
        cross_sectional_ranking = evaluate_cross_sectional_ranking(
            mint_frame,
            min_train_decision_times=min_train_decision_times,
            validation_decision_times=validation_decision_times,
            step_decision_times=step_decision_times,
            min_train_rows=min_train_rows,
        )
    except ValueError as exc:
        cross_sectional_ranking_reason = str(exc)

    rotation_evidence = None
    rotation_evidence_reason = None
    try:
        rotation_evidence = evaluate_rotation_transition_evidence(
            mint_frame,
            min_train_decision_times=min_train_decision_times,
            validation_decision_times=validation_decision_times,
            step_decision_times=step_decision_times,
            min_train_rows=min_train_rows,
        )
    except ValueError as exc:
        rotation_evidence_reason = str(exc)

    return MintFeatureResearchReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        status="EVALUATED",
        reason=None,
        source_dataset_rows=len(source),
        source_dataset_sha256=source_sha,
        market_enrichment=market_report,
        mint_enrichment=mint_report,
        ablation=ablation,
        context_ablation=context_ablation,
        unseen_pool_validation=unseen_pool_validation,
        unseen_pool_reason=unseen_pool_reason,
        tail_risk_calibration=tail_risk_calibration,
        tail_risk_reason=tail_risk_reason,
        feature_drift=feature_drift,
        feature_drift_reason=feature_drift_reason,
        source_outcome_coverage=source_outcome_coverage,
        model_ready_outcome_coverage=model_ready_outcome_coverage,
        api_metadata_enrichment=api_metadata_enrichment,
        api_metadata_ablation=api_metadata_ablation,
        api_metadata_reason=api_metadata_reason,
        execution_cost_enrichment=execution_cost_enrichment,
        execution_cost_ablation=execution_cost_ablation,
        execution_cost_reason=execution_cost_reason,
        cross_sectional_ranking=cross_sectional_ranking,
        cross_sectional_ranking_reason=cross_sectional_ranking_reason,
        rotation_evidence=rotation_evidence,
        rotation_evidence_reason=rotation_evidence_reason,
    )
