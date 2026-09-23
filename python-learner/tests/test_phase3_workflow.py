from meteora_learner.baseline_walk_forward import BaselineWalkForwardReport
from meteora_learner.phase3_validation import Phase3PromotionCriteria
from meteora_learner.phase3_workflow import (
    Phase3ValidationInput,
    validate_phase3_from_chain,
)
from meteora_learner.phase_promotion import (
    PHASE3,
    persist_phase2_promotion,
    phase_promotion_state,
)
from meteora_learner.storage import Storage
from types import SimpleNamespace
import pytest


def ready_report():
    return SimpleNamespace(
        promotion_ready=True,
        to_record=lambda: {"promotion_ready": True},
    )


def fake_walk_report(pool):
    return BaselineWalkForwardReport(
        pool_address=pool,
        observation_count=4,
        lookback_observations=2,
        forward_observations=2,
        step_observations=1,
        steps=2,
        selected_steps=2,
        forward_valid_steps=2,
        economically_complete_steps=2,
        positive_excess_steps=2,
        negative_excess_steps=0,
        zero_excess_steps=0,
        positive_excess_rate=1.0,
        total_excess_vs_hold_y_atomic=2,
        mean_excess_vs_hold_bps=10.0,
        worst_excess_vs_hold_bps=10,
        best_excess_vs_hold_bps=10,
        phase2_ready=False,
        research_only=True,
        steps_detail=(
            SimpleNamespace(
                forward_status="VALID",
                forward_economics=SimpleNamespace(excess_vs_hold_bps=10),
            ),
            SimpleNamespace(
                forward_status="VALID",
                forward_economics=SimpleNamespace(excess_vs_hold_bps=10),
            ),
        ),
    )


def test_phase3_workflow_uses_persisted_phase2_and_can_persist_phase3(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    persist_phase2_promotion(storage, report=ready_report())

    monkeypatch.setattr(
        "meteora_learner.phase3_workflow.walk_forward_baseline",
        lambda database_path, **kwargs: fake_walk_report(kwargs["pool_address"]),
    )

    result = validate_phase3_from_chain(
        str(storage.path),
        inputs=(
            Phase3ValidationInput("a", 0, 10, 0),
            Phase3ValidationInput("b", 0, 10, 0),
        ),
        criteria=Phase3PromotionCriteria(
            min_pools=2,
            min_total_steps=4,
            min_complete_steps=4,
            min_selection_rate=1.0,
            min_complete_rate=1.0,
            min_positive_excess_rate=1.0,
            min_mean_excess_vs_hold_bps=0,
            max_single_step_loss_bps=100,
        ),
        persist_if_ready=True,
        lookback_observations=2,
        forward_observations=2,
    )

    assert result.phase2_ready is True
    assert result.promotion.promotion_ready is True
    assert result.persisted is True
    assert phase_promotion_state(
        storage,
        phase_name=PHASE3,
    ).promoted is True


def test_phase3_workflow_cannot_pass_without_persisted_phase2(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        "meteora_learner.phase3_workflow.walk_forward_baseline",
        lambda database_path, **kwargs: fake_walk_report(kwargs["pool_address"]),
    )

    result = validate_phase3_from_chain(
        str(storage.path),
        inputs=(Phase3ValidationInput("a", 0, 10, 0),),
        criteria=Phase3PromotionCriteria(
            min_pools=1,
            min_total_steps=1,
            min_complete_steps=1,
            min_selection_rate=0,
            min_complete_rate=0,
            min_positive_excess_rate=0,
            min_mean_excess_vs_hold_bps=-1000,
            max_single_step_loss_bps=1000,
        ),
    )

    assert result.phase2_ready is False
    assert result.promotion.promotion_ready is False
    assert any("Phase 2" in reason for reason in result.promotion.reasons)
