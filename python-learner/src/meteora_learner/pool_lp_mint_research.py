from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

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
        )

    mint_frame, mint_report = build_mint_enriched_lp_training_frame(
        database_path,
        market_frame,
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
        )

    try:
        ablation = evaluate_mint_feature_ablation(
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
        )

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
    )
