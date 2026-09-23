import pandas as pd
import pytest

from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.ml_registry import (
    OFFLINE_QUALIFIED,
    PAPER_CHALLENGER,
    register_ml_v1_bundle,
    transition_model,
)
from meteora_learner.ml_training import train_ml_v1_frame
from meteora_learner.paper_account import (
    close_paper_position,
    create_paper_account,
    open_paper_position,
)
from meteora_learner.paper_challenger import (
    PaperChallengerCriteria,
    evaluate_paper_challenger,
    promote_paper_challenger,
)
from meteora_learner.paper_performance import build_paper_performance
from meteora_learner.storage import Storage


def training_frame(rows=60):
    records = []
    for index in range(rows):
        positive = index % 2 == 0
        row = {
            "pool_address": f"pool-{index % 3}",
            "decision_observed_at": (
                pd.Timestamp("2026-01-01", tz="UTC")
                + pd.Timedelta(hours=index)
            ).isoformat(),
            "forward_end_observed_at": (
                pd.Timestamp("2026-01-01", tz="UTC")
                + pd.Timedelta(hours=index + 1)
            ).isoformat(),
            "strategy": "SPOT",
            "baseline_selected": 1,
            "target_net_return_bps": 20 if positive else -10,
            "target_excess_vs_hold_bps": 15 if positive else -15,
            "target_range_survival_ratio": 0.8 if positive else 0.4,
            "target_positive_excess": int(positive),
        }
        for feature_index, column in enumerate(ML_FEATURE_COLUMNS):
            row[column] = float((index + feature_index) % 9)
        records.append(row)
    return pd.DataFrame(records)


def close_trade(storage, *, account, position, source, pnl, model_id=None):
    capital = 100.0
    open_paper_position(
        storage,
        event_key=f"{position}-enter",
        account_id=account,
        position_id=position,
        pool_address="pool",
        policy_source=source,
        model_id=model_id,
        strategy="SPOT",
        min_bin_id=-1,
        max_bin_id=1,
        capital_quote=capital,
    )
    close_paper_position(
        storage,
        event_key=f"{position}-exit",
        position_id=position,
        final_mark_quote=capital + pnl,
    )


def test_paper_performance_and_challenger_gate(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=10_000,
    )

    bundle = train_ml_v1_frame(training_frame(), min_rows=50)
    register_ml_v1_bundle(
        storage,
        model_id="model-a",
        bundle=bundle,
        dataset_version="dataset-v1",
    )
    transition_model(
        storage,
        model_id="model-a",
        new_status=OFFLINE_QUALIFIED,
    )
    transition_model(
        storage,
        model_id="model-a",
        new_status=PAPER_CHALLENGER,
    )

    for index, pnl in enumerate((2, 3, -1, 4)):
        close_trade(
            storage,
            account="paper",
            position=f"base-{index}",
            source="DETERMINISTIC",
            pnl=pnl,
        )
    for index, pnl in enumerate((5, 4, -1, 6)):
        close_trade(
            storage,
            account="paper",
            position=f"ml-{index}",
            source="ML_CHALLENGER",
            model_id="model-a",
            pnl=pnl,
        )

    performance = build_paper_performance(
        storage,
        account_id="paper",
        policy_source="ML_CHALLENGER",
        model_id="model-a",
    )
    assert performance.closed_trades == 4
    assert performance.realized_return_bps == 350
    assert performance.winning_trades == 3

    validation = evaluate_paper_challenger(
        storage,
        account_id="paper",
        model_id="model-a",
        phase3_ready=True,
        criteria=PaperChallengerCriteria(
            min_closed_trades=4,
            min_realized_return_bps=0,
            min_win_rate=0.50,
            max_realized_drawdown_bps=500,
            min_return_uplift_vs_baseline_bps=100,
        ),
    )
    assert validation.paper_qualified is True
    assert validation.return_uplift_vs_baseline_bps == 150
    assert validation.policy_actionable is False

    promoted = promote_paper_challenger(
        storage,
        validation=validation,
    )
    assert promoted.status == "CHAMPION"


def test_paper_challenger_cannot_qualify_before_phase3(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    storage.register_model(
        model_id="model",
        model_family="ML_V1",
        feature_version="features-v1",
        dataset_version="dataset-v1",
        metrics={},
    )
    storage.update_model_status(
        "model",
        expected_status="OFFLINE_CANDIDATE",
        new_status="OFFLINE_QUALIFIED",
    )
    storage.update_model_status(
        "model",
        expected_status="OFFLINE_QUALIFIED",
        new_status="PAPER_CHALLENGER",
    )

    validation = evaluate_paper_challenger(
        storage,
        account_id="paper",
        model_id="model",
        phase3_ready=False,
        criteria=PaperChallengerCriteria(min_closed_trades=1),
    )
    assert validation.paper_qualified is False
    assert "Phase 3" in validation.reasons[0]

    with pytest.raises(ValueError, match="has not passed"):
        promote_paper_challenger(storage, validation=validation)
