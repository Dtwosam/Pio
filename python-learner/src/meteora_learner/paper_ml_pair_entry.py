from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import math
from typing import Any, Sequence

from .ml_current_candidates import (
    MLCurrentCandidateFrameReport,
    build_current_ml_candidate_frame,
)
from .ml_inference import (
    MLInferenceConfig,
    MLInferenceReport,
    MLCandidatePrediction,
    score_ml_candidates,
)
from .ml_workflow import load_registered_ml_v1
from .paper_account import (
    PaperAccountSnapshot,
    PaperPositionSnapshot,
    _open_paper_position_in_conn,
    paper_account_snapshot,
    paper_position_snapshot,
)
from .paper_chain_valuation import (
    CounterfactualPlanPreview,
    _insert_counterfactual_preview,
    prepare_counterfactual_candidate,
)
from .retraining_cycle import retraining_cycle
from .storage import Storage
from .strategy import StrategyType


MODEL_FAMILY = "ML_V1_HIST_GRADIENT_BOOSTING"
FEATURE_VERSION = "ML_ACTION_FEATURES_V1"


@dataclass(frozen=True)
class PairedMLPaperChoice:
    model_id: str
    policy_source: str
    row_index: int
    pool_address: str
    decision_observed_at: str
    strategy: str
    half_width: int
    center_offset: int
    min_bin_id: int
    max_bin_id: int
    risk_adjusted_score_bps: float
    predicted_positive_excess_probability: float
    predicted_range_survival: float


@dataclass(frozen=True)
class PairedMLPaperEntryResult:
    account_id: str
    cycle_id: str
    pool_address: str
    decision_observed_at: str
    incumbent_model_id: str
    challenger_model_id: str
    incumbent_position_id: str
    challenger_position_id: str
    incumbent_choice: PairedMLPaperChoice
    challenger_choice: PairedMLPaperChoice
    incumbent_position: PaperPositionSnapshot
    challenger_position: PaperPositionSnapshot
    account: PaperAccountSnapshot
    candidate_frame: dict[str, Any]
    incumbent_inference: dict[str, Any]
    challenger_inference: dict[str, Any]
    equal_capital_quote: float
    equal_entry_cost_quote: float
    same_candidate_frame: bool
    same_decision_snapshot: bool
    equal_capital: bool
    atomic_pair_open: bool
    chain_bound: bool
    no_lookahead: bool
    paper_only: bool
    policy_actionable: bool
    live_authorized: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _decimal(
    value: float,
    *,
    label: str,
    positive: bool,
) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError(f"{label} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{label} must be positive")
    if not positive and result < 0:
        raise ValueError(f"{label} cannot be negative")
    return result


def _model_status_record(
    storage: Storage,
    *,
    model_id: str,
    required_status: str,
) -> dict[str, Any]:
    raw = storage.model_registry_entry(model_id)
    if raw is None:
        raise ValueError(f"unknown ML model_id: {model_id}")
    if str(raw.get("status")) != required_status:
        raise ValueError(
            f"model {model_id} must be {required_status}"
        )
    if str(raw.get("model_family")) != MODEL_FAMILY:
        raise ValueError(
            f"model {model_id} family is not {MODEL_FAMILY}"
        )
    if str(raw.get("feature_version")) != FEATURE_VERSION:
        raise ValueError(
            f"model {model_id} feature_version is not {FEATURE_VERSION}"
        )
    return raw


def _choice(
    *,
    model_id: str,
    policy_source: str,
    prediction: MLCandidatePrediction,
    frame: Any,
) -> PairedMLPaperChoice:
    if prediction.row_index not in frame.index:
        raise ValueError(
            f"{policy_source} inference choice row is missing"
        )
    row = frame.loc[prediction.row_index]
    if (
        str(row["pool_address"]) != prediction.pool_address
        or str(row["decision_observed_at"])
        != prediction.decision_observed_at
        or str(row["strategy"]) != prediction.strategy
        or int(row["half_width"]) != prediction.half_width
        or int(row["center_offset"]) != prediction.center_offset
    ):
        raise ValueError(
            f"{policy_source} inference choice/frame binding mismatch"
        )
    active_bin_id = int(row["active_bin_id"])
    center = active_bin_id + prediction.center_offset
    return PairedMLPaperChoice(
        model_id=model_id,
        policy_source=policy_source,
        row_index=prediction.row_index,
        pool_address=prediction.pool_address,
        decision_observed_at=prediction.decision_observed_at,
        strategy=prediction.strategy,
        half_width=prediction.half_width,
        center_offset=prediction.center_offset,
        min_bin_id=center - prediction.half_width,
        max_bin_id=center + prediction.half_width,
        risk_adjusted_score_bps=prediction.risk_adjusted_score_bps,
        predicted_positive_excess_probability=(
            prediction.predicted_positive_excess_probability
        ),
        predicted_range_survival=prediction.predicted_range_survival,
    )


def _preflight_choice(
    storage: Storage,
    *,
    choice: PairedMLPaperChoice,
    amount_x: int,
    amount_y: int,
    max_share_bps: int,
    favor_x_in_active_bin: bool,
) -> CounterfactualPlanPreview:
    preview = prepare_counterfactual_candidate(
        storage,
        pool_address=choice.pool_address,
        decision_observed_at=choice.decision_observed_at,
        amount_x=amount_x,
        amount_y=amount_y,
        strategy=choice.strategy,
        min_bin_id=choice.min_bin_id,
        max_bin_id=choice.max_bin_id,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )
    if preview.entry_observed_at != choice.decision_observed_at:
        raise ValueError(
            f"{choice.policy_source} counterfactual decision time drifted"
        )
    if (
        preview.strategy != choice.strategy
        or preview.min_bin_id != choice.min_bin_id
        or preview.max_bin_id != choice.max_bin_id
    ):
        raise ValueError(
            f"{choice.policy_source} counterfactual choice drifted"
        )
    return preview


def _verify_pair_transaction_state(
    conn: Any,
    *,
    cycle_id: str,
    incumbent_model_id: str,
    challenger_model_id: str,
) -> None:
    cycle = conn.execute(
        """
        SELECT status, active_key, champion_model_id, challenger_model_id
        FROM continuous_learning_cycles
        WHERE cycle_id = ?
        """,
        (cycle_id,),
    ).fetchone()
    if cycle is None:
        raise ValueError(f"unknown retraining cycle: {cycle_id}")
    if str(cycle[0]) != "PAPER_CHALLENGER":
        raise ValueError(
            "paired ML PAPER entry requires PAPER_CHALLENGER cycle status"
        )
    if str(cycle[1]) != "ACTIVE":
        raise ValueError(
            "paired ML PAPER entry requires the active retraining cycle"
        )
    if str(cycle[2]) != incumbent_model_id:
        raise ValueError(
            "paired ML PAPER entry incumbent/cycle binding changed"
        )
    if str(cycle[3]) != challenger_model_id:
        raise ValueError(
            "paired ML PAPER entry challenger/cycle binding changed"
        )

    statuses = dict(
        conn.execute(
            """
            SELECT model_id, status
            FROM model_registry
            WHERE model_id IN (?, ?)
            """,
            (incumbent_model_id, challenger_model_id),
        ).fetchall()
    )
    if statuses.get(incumbent_model_id) != "CHAMPION":
        raise ValueError(
            "paired ML PAPER entry incumbent is no longer CHAMPION"
        )
    if statuses.get(challenger_model_id) != "PAPER_CHALLENGER":
        raise ValueError(
            "paired ML PAPER entry challenger is no longer PAPER_CHALLENGER"
        )


def open_paired_ml_paper_entries(
    storage: Storage,
    *,
    account_id: str,
    cycle_id: str,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    network_cost_y_atomic: int,
    capital_quote: float,
    incumbent_position_id: str,
    challenger_position_id: str,
    incumbent_event_key: str,
    challenger_event_key: str,
    entry_cost_quote: float = 0.0,
    lookback_observations: int = 12,
    half_widths: Sequence[int] = (0, 1, 2, 5, 10),
    center_offsets: Sequence[int] = (0,),
    strategies: Sequence[StrategyType | str] = (
        StrategyType.SPOT,
        StrategyType.CURVE,
        StrategyType.BID_ASK,
    ),
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
    near_liquidity_radius: int = 5,
    inference_config: MLInferenceConfig = MLInferenceConfig(),
    as_of: str | None = None,
) -> PairedMLPaperEntryResult:
    """
    Open one fair incumbent/challenger PAPER pair from the same current frame.

    Both registered models score the exact same trailing-only candidate frame.
    Both entries use the same pool, decision snapshot, atomic token inputs,
    simulated capital and entry cost. The two paper positions and their
    counterfactual chain bindings commit atomically.
    """
    if not account_id.strip():
        raise ValueError("account_id is required")
    if not cycle_id.strip():
        raise ValueError("cycle_id is required")
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if not incumbent_position_id.strip() or not challenger_position_id.strip():
        raise ValueError("both PAPER position ids are required")
    if incumbent_position_id == challenger_position_id:
        raise ValueError("paired PAPER position ids must be distinct")
    if not incumbent_event_key.strip() or not challenger_event_key.strip():
        raise ValueError("both PAPER event keys are required")
    if incumbent_event_key == challenger_event_key:
        raise ValueError("paired PAPER event keys must be distinct")
    if amount_x < 0 or amount_y < 0 or (amount_x == 0 and amount_y == 0):
        raise ValueError(
            "at least one non-negative token amount must be positive"
        )
    if network_cost_y_atomic < 0:
        raise ValueError("network_cost_y_atomic cannot be negative")
    capital = _decimal(
        capital_quote,
        label="capital_quote",
        positive=True,
    )
    cost = _decimal(
        entry_cost_quote,
        label="entry_cost_quote",
        positive=False,
    )

    cycle = retraining_cycle(storage, cycle_id=cycle_id)
    if cycle.status != "PAPER_CHALLENGER":
        raise ValueError(
            "paired ML PAPER entry requires PAPER_CHALLENGER cycle status"
        )
    if cycle.challenger_model_id is None:
        raise ValueError("PAPER challenger cycle has no challenger model")
    incumbent_model_id = cycle.champion_model_id
    challenger_model_id = cycle.challenger_model_id
    if incumbent_model_id == challenger_model_id:
        raise ValueError(
            "incumbent and challenger model ids must be distinct"
        )

    incumbent_raw = _model_status_record(
        storage,
        model_id=incumbent_model_id,
        required_status="CHAMPION",
    )
    challenger_raw = _model_status_record(
        storage,
        model_id=challenger_model_id,
        required_status="PAPER_CHALLENGER",
    )
    if str(challenger_raw.get("dataset_version")) != cycle.target_dataset_version:
        raise ValueError(
            "challenger dataset_version does not match retraining cycle"
        )

    account_before = paper_account_snapshot(
        storage,
        account_id=account_id,
    )
    required_cash = 2 * (capital + cost)
    if Decimal(str(account_before.cash_quote)) < required_cash:
        raise ValueError(
            "paper account cash is insufficient for equal paired entries"
        )

    candidate_report: MLCurrentCandidateFrameReport = (
        build_current_ml_candidate_frame(
            str(storage.path),
            pool_address=pool_address,
            amount_x=amount_x,
            amount_y=amount_y,
            network_cost_y_atomic=network_cost_y_atomic,
            lookback_observations=lookback_observations,
            half_widths=half_widths,
            center_offsets=center_offsets,
            strategies=strategies,
            max_share_bps=max_share_bps,
            favor_x_in_active_bin=favor_x_in_active_bin,
            near_liquidity_radius=near_liquidity_radius,
            as_of=as_of,
        )
    )
    if candidate_report.no_lookahead is not True:
        raise ValueError(
            "paired ML PAPER entry candidate frame crossed no-lookahead boundary"
        )
    frame = candidate_report.to_frame()

    incumbent_bundle = load_registered_ml_v1(
        storage,
        model_id=incumbent_model_id,
    )
    challenger_bundle = load_registered_ml_v1(
        storage,
        model_id=challenger_model_id,
    )
    incumbent_inference: MLInferenceReport = score_ml_candidates(
        incumbent_bundle,
        frame,
        config=inference_config,
    )
    challenger_inference: MLInferenceReport = score_ml_candidates(
        challenger_bundle,
        frame,
        config=inference_config,
    )
    if (
        incumbent_inference.policy_actionable is not False
        or challenger_inference.policy_actionable is not False
    ):
        raise ValueError(
            "paired ML PAPER inference became policy-actionable"
        )
    if incumbent_inference.research_choice is None:
        raise ValueError("incumbent model has no eligible PAPER choice")
    if challenger_inference.research_choice is None:
        raise ValueError("challenger model has no eligible PAPER choice")

    incumbent_choice = _choice(
        model_id=incumbent_model_id,
        policy_source="ML_CHAMPION",
        prediction=incumbent_inference.research_choice,
        frame=frame,
    )
    challenger_choice = _choice(
        model_id=challenger_model_id,
        policy_source="ML_CHALLENGER",
        prediction=challenger_inference.research_choice,
        frame=frame,
    )
    if (
        incumbent_choice.pool_address != pool_address
        or challenger_choice.pool_address != pool_address
    ):
        raise ValueError("paired ML PAPER inference changed pool")
    if (
        incumbent_choice.decision_observed_at
        != candidate_report.decision_observed_at
        or challenger_choice.decision_observed_at
        != candidate_report.decision_observed_at
    ):
        raise ValueError(
            "paired ML PAPER inference changed decision snapshot"
        )

    incumbent_preview = _preflight_choice(
        storage,
        choice=incumbent_choice,
        amount_x=amount_x,
        amount_y=amount_y,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )
    challenger_preview = _preflight_choice(
        storage,
        choice=challenger_choice,
        amount_x=amount_x,
        amount_y=amount_y,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )

    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _verify_pair_transaction_state(
            conn,
            cycle_id=cycle_id,
            incumbent_model_id=incumbent_model_id,
            challenger_model_id=challenger_model_id,
        )
        _open_paper_position_in_conn(
            conn,
            event_key=incumbent_event_key,
            account_id=account_id,
            position_id=incumbent_position_id,
            pool_address=pool_address,
            policy_source="ML_CHAMPION",
            strategy=incumbent_choice.strategy,
            min_bin_id=incumbent_choice.min_bin_id,
            max_bin_id=incumbent_choice.max_bin_id,
            capital=capital,
            cost=cost,
            model_id=incumbent_model_id,
            event_time=candidate_report.decision_observed_at,
        )
        _insert_counterfactual_preview(
            conn,
            position_id=incumbent_position_id,
            capital_quote=float(capital),
            preview=incumbent_preview,
        )
        _open_paper_position_in_conn(
            conn,
            event_key=challenger_event_key,
            account_id=account_id,
            position_id=challenger_position_id,
            pool_address=pool_address,
            policy_source="ML_CHALLENGER",
            strategy=challenger_choice.strategy,
            min_bin_id=challenger_choice.min_bin_id,
            max_bin_id=challenger_choice.max_bin_id,
            capital=capital,
            cost=cost,
            model_id=challenger_model_id,
            event_time=candidate_report.decision_observed_at,
        )
        _insert_counterfactual_preview(
            conn,
            position_id=challenger_position_id,
            capital_quote=float(capital),
            preview=challenger_preview,
        )

    incumbent_position = paper_position_snapshot(
        storage,
        position_id=incumbent_position_id,
    )
    challenger_position = paper_position_snapshot(
        storage,
        position_id=challenger_position_id,
    )
    account_after = paper_account_snapshot(
        storage,
        account_id=account_id,
    )

    for position, policy_source, model_id in (
        (
            incumbent_position,
            "ML_CHAMPION",
            incumbent_model_id,
        ),
        (
            challenger_position,
            "ML_CHALLENGER",
            challenger_model_id,
        ),
    ):
        if (
            position.status != "OPEN"
            or position.account_id != account_id
            or position.pool_address != pool_address
            or position.policy_source != policy_source
            or position.model_id != model_id
            or not math.isclose(
                position.entry_capital_quote,
                float(capital),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            raise RuntimeError(
                "paired ML PAPER position verification failed"
            )

    return PairedMLPaperEntryResult(
        account_id=account_id,
        cycle_id=cycle_id,
        pool_address=pool_address,
        decision_observed_at=candidate_report.decision_observed_at,
        incumbent_model_id=incumbent_model_id,
        challenger_model_id=challenger_model_id,
        incumbent_position_id=incumbent_position_id,
        challenger_position_id=challenger_position_id,
        incumbent_choice=incumbent_choice,
        challenger_choice=challenger_choice,
        incumbent_position=incumbent_position,
        challenger_position=challenger_position,
        account=account_after,
        candidate_frame=candidate_report.to_record(),
        incumbent_inference=incumbent_inference.to_record(),
        challenger_inference=challenger_inference.to_record(),
        equal_capital_quote=float(capital),
        equal_entry_cost_quote=float(cost),
        same_candidate_frame=True,
        same_decision_snapshot=True,
        equal_capital=True,
        atomic_pair_open=True,
        chain_bound=True,
        no_lookahead=True,
        paper_only=True,
        policy_actionable=False,
        live_authorized=False,
    )
