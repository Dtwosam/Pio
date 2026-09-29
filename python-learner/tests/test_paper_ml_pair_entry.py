from __future__ import annotations

import copy
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from meteora_learner import paper_ml_pair_entry as MODULE
from meteora_learner.ml_current_candidates import MLCurrentCandidateFrameReport
from meteora_learner.ml_inference import (
    MLCandidatePrediction,
    MLInferenceReport,
)
from meteora_learner.paper_account import (
    PaperAccountSnapshot,
    PaperPositionSnapshot,
)


ROOT = Path(__file__).resolve().parents[2]
INCUMBENT = "champion-1"
CHALLENGER = "challenger-1"
CYCLE = "cycle-1"
ACCOUNT = "paper-1"
DECISION = "2026-09-29T22:30:00+00:00"


class FakeStorage:
    def __init__(self, path: Path):
        self.path = path

    def connect(self):
        return sqlite3.connect(self.path)

    def model_registry_entry(self, model_id: str):
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT model_id, status, model_family,
                       feature_version, dataset_version
                FROM model_registry
                WHERE model_id = ?
                """,
                (model_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "model_id": row[0],
            "status": row[1],
            "model_family": row[2],
            "feature_version": row[3],
            "dataset_version": row[4],
            "artifact_uri": f"/trusted/{model_id}.joblib",
        }


def _seed(path: Path) -> FakeStorage:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE model_registry(
                model_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                model_family TEXT NOT NULL,
                feature_version TEXT NOT NULL,
                dataset_version TEXT NOT NULL
            );
            CREATE TABLE continuous_learning_cycles(
                cycle_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                active_key TEXT,
                champion_model_id TEXT NOT NULL,
                challenger_model_id TEXT
            );
            CREATE TABLE paper_accounts(
                account_id TEXT PRIMARY KEY,
                cash_quote REAL NOT NULL
            );
            CREATE TABLE pair_positions(
                position_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                status TEXT NOT NULL,
                policy_source TEXT NOT NULL,
                model_id TEXT NOT NULL,
                strategy TEXT NOT NULL,
                min_bin_id INTEGER NOT NULL,
                max_bin_id INTEGER NOT NULL,
                capital_quote REAL NOT NULL,
                entry_cost_quote REAL NOT NULL,
                event_time TEXT NOT NULL,
                event_key TEXT NOT NULL UNIQUE
            );
            CREATE TABLE pair_previews(
                position_id TEXT PRIMARY KEY
            );
            """
        )
        conn.execute(
            """
            INSERT INTO model_registry
            VALUES (?, 'CHAMPION', ?, ?, 'dataset-v1')
            """,
            (INCUMBENT, MODULE.MODEL_FAMILY, MODULE.FEATURE_VERSION),
        )
        conn.execute(
            """
            INSERT INTO model_registry
            VALUES (?, 'PAPER_CHALLENGER', ?, ?, 'dataset-v2')
            """,
            (CHALLENGER, MODULE.MODEL_FAMILY, MODULE.FEATURE_VERSION),
        )
        conn.execute(
            """
            INSERT INTO continuous_learning_cycles
            VALUES (?, 'PAPER_CHALLENGER', 'ACTIVE', ?, ?)
            """,
            (CYCLE, INCUMBENT, CHALLENGER),
        )
        conn.execute(
            "INSERT INTO paper_accounts VALUES (?, 10000.0)",
            (ACCOUNT,),
        )
        conn.commit()
    finally:
        conn.close()
    return FakeStorage(path)


def _frame_report() -> MLCurrentCandidateFrameReport:
    base = {
        "pool_address": "pool-1",
        "decision_observed_at": DECISION,
        "baseline_selected": 0,
        "strategy_spot": 1,
        "strategy_curve": 0,
        "strategy_bid_ask": 0,
        "range_width_bins": 3,
        "active_bin_id": 100,
        "active_bin_move_1": 1,
        "deposit_fee_rate_bps": 10.0,
        "occupied_bins": 5,
        "active_liquidity_ratio": 0.25,
        "near_active_liquidity_ratio": 0.8,
        "below_active_liquidity_ratio": 0.4,
        "above_active_liquidity_ratio": 0.6,
        "liquidity_weighted_distance_bins": 1.2,
        "fee_growth_bins_x": 2,
        "fee_growth_bins_y": 3,
        "trailing_range_survival_ratio": 0.75,
        "trailing_excess_vs_hold_bps": 100,
        "trailing_net_return_bps": 80,
        "trailing_max_observed_share_bps": 50,
    }
    incumbent = {
        **base,
        "strategy": "SPOT",
        "half_width": 1,
        "center_offset": 0,
    }
    challenger = {
        **base,
        "strategy": "CURVE",
        "strategy_spot": 0,
        "strategy_curve": 1,
        "half_width": 2,
        "center_offset": 1,
        "range_width_bins": 5,
    }
    return MLCurrentCandidateFrameReport(
        pool_address="pool-1",
        decision_observed_at=DECISION,
        previous_observed_at="2026-09-29T22:25:00+00:00",
        lookback_observations=12,
        candidates_seen=2,
        candidates_built=2,
        candidates_dropped=0,
        drop_reasons=(),
        rows=(incumbent, challenger),
        no_lookahead=True,
    )


def _prediction(
    *,
    row_index: int,
    strategy: str,
    half_width: int,
    center_offset: int,
) -> MLCandidatePrediction:
    return MLCandidatePrediction(
        row_index=row_index,
        pool_address="pool-1",
        decision_observed_at=DECISION,
        strategy=strategy,
        half_width=half_width,
        center_offset=center_offset,
        predicted_net_return_bps=100.0,
        predicted_excess_vs_hold_bps=80.0,
        predicted_downside_bps=10.0,
        predicted_range_survival=0.8,
        predicted_positive_excess_probability=0.7,
        risk_adjusted_score_bps=65.0,
        eligible=True,
        rejection_reasons=(),
    )


def _report(choice: MLCandidatePrediction) -> MLInferenceReport:
    return MLInferenceReport(
        candidates_seen=2,
        candidates_eligible=1,
        research_choice=choice,
        policy_actionable=False,
        ranking_rule="test",
        predictions=(choice,),
    )


def _account_snapshot(storage: FakeStorage, *, account_id: str):
    with storage.connect() as conn:
        cash = conn.execute(
            "SELECT cash_quote FROM paper_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        open_count = conn.execute(
            "SELECT COUNT(*) FROM pair_positions WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    if cash is None:
        raise ValueError(f"unknown paper account: {account_id}")
    return PaperAccountSnapshot(
        account_id=account_id,
        starting_equity_quote=10000.0,
        cash_quote=float(cash[0]),
        open_position_mark_quote=0.0,
        open_fee_income_quote=0.0,
        open_reward_income_quote=0.0,
        account_equity_quote=float(cash[0]),
        high_water_equity_quote=10000.0,
        drawdown_bps=0,
        open_positions=int(open_count[0]),
        closed_positions=0,
        realized_pnl_quote=0.0,
    )


def _position_snapshot(storage: FakeStorage, *, position_id: str):
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT position_id, account_id, pool_address, status,
                   policy_source, model_id, strategy, min_bin_id,
                   max_bin_id, capital_quote, entry_cost_quote
            FROM pair_positions WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"unknown paper position: {position_id}")
    return PaperPositionSnapshot(
        position_id=row[0],
        account_id=row[1],
        pool_address=row[2],
        status=row[3],
        policy_source=row[4],
        model_id=row[5],
        strategy=row[6],
        min_bin_id=int(row[7]),
        max_bin_id=int(row[8]),
        entry_capital_quote=float(row[9]),
        entry_cost_quote=float(row[10]),
        current_mark_quote=float(row[9]),
        fee_income_quote=0.0,
        reward_income_quote=0.0,
        rebalance_cost_quote=0.0,
        exit_cost_quote=0.0,
        realized_pnl_quote=None,
        rebalances=0,
    )


def _open_in_conn(
    conn,
    *,
    event_key,
    account_id,
    position_id,
    pool_address,
    policy_source,
    strategy,
    min_bin_id,
    max_bin_id,
    capital,
    cost,
    model_id,
    event_time,
):
    row = conn.execute(
        "SELECT cash_quote FROM paper_accounts WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    debit = float(capital + cost)
    if row is None or float(row[0]) < debit:
        raise ValueError("insufficient paper cash for entry and cost")
    conn.execute(
        "UPDATE paper_accounts SET cash_quote = cash_quote - ? WHERE account_id = ?",
        (debit, account_id),
    )
    conn.execute(
        """
        INSERT INTO pair_positions(
            position_id, account_id, pool_address, status,
            policy_source, model_id, strategy, min_bin_id, max_bin_id,
            capital_quote, entry_cost_quote, event_time, event_key
        ) VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            position_id,
            account_id,
            pool_address,
            policy_source,
            model_id,
            strategy,
            min_bin_id,
            max_bin_id,
            float(capital),
            float(cost),
            event_time,
            event_key,
        ),
    )


def _preview(choice):
    return SimpleNamespace(
        entry_observed_at=DECISION,
        strategy=choice.strategy,
        min_bin_id=choice.min_bin_id,
        max_bin_id=choice.max_bin_id,
    )


def _install(monkeypatch, storage: FakeStorage, *, fail_second_bind=False):
    frame_report = _frame_report()
    frame_ids = []
    incumbent_choice = _prediction(
        row_index=0,
        strategy="SPOT",
        half_width=1,
        center_offset=0,
    )
    challenger_choice = _prediction(
        row_index=1,
        strategy="CURVE",
        half_width=2,
        center_offset=1,
    )

    monkeypatch.setattr(
        MODULE,
        "retraining_cycle",
        lambda storage_value, cycle_id: SimpleNamespace(
            cycle_id=CYCLE,
            status="PAPER_CHALLENGER",
            champion_model_id=INCUMBENT,
            challenger_model_id=CHALLENGER,
            target_dataset_version="dataset-v2",
        ),
    )
    monkeypatch.setattr(
        MODULE,
        "paper_account_snapshot",
        _account_snapshot,
    )
    monkeypatch.setattr(
        MODULE,
        "paper_position_snapshot",
        _position_snapshot,
    )
    monkeypatch.setattr(
        MODULE,
        "build_current_ml_candidate_frame",
        lambda *args, **kwargs: frame_report,
    )
    monkeypatch.setattr(
        MODULE,
        "load_registered_ml_v1",
        lambda storage_value, model_id: model_id,
    )

    def score(bundle, frame, *, config):
        frame_ids.append(id(frame))
        return _report(
            incumbent_choice if bundle == INCUMBENT else challenger_choice
        )

    monkeypatch.setattr(MODULE, "score_ml_candidates", score)

    def preflight(storage_value, *, choice, **kwargs):
        return _preview(choice)

    monkeypatch.setattr(MODULE, "_preflight_choice", preflight)
    monkeypatch.setattr(MODULE, "_open_paper_position_in_conn", _open_in_conn)

    def bind(conn, *, position_id, capital_quote, preview):
        if fail_second_bind and position_id == "challenger-pos":
            raise ValueError("forced challenger bind failure")
        conn.execute(
            "INSERT INTO pair_previews(position_id) VALUES (?)",
            (position_id,),
        )

    monkeypatch.setattr(MODULE, "_insert_counterfactual_preview", bind)
    return frame_ids


def _run(storage: FakeStorage):
    return MODULE.open_paired_ml_paper_entries(
        storage,
        account_id=ACCOUNT,
        cycle_id=CYCLE,
        pool_address="pool-1",
        amount_x=100,
        amount_y=200,
        network_cost_y_atomic=3,
        capital_quote=1000.0,
        incumbent_position_id="incumbent-pos",
        challenger_position_id="challenger-pos",
        incumbent_event_key="incumbent-event",
        challenger_event_key="challenger-event",
        entry_cost_quote=5.0,
        as_of=DECISION,
    )


def test_paired_entry_scores_exact_same_frame_and_opens_equal_capital(
    tmp_path,
    monkeypatch,
):
    storage = _seed(tmp_path / "pio.db")
    frame_ids = _install(monkeypatch, storage)

    result = _run(storage)

    assert len(frame_ids) == 2
    assert frame_ids[0] == frame_ids[1]
    assert result.same_candidate_frame is True
    assert result.same_decision_snapshot is True
    assert result.equal_capital is True
    assert result.equal_capital_quote == 1000.0
    assert result.equal_entry_cost_quote == 5.0
    assert result.atomic_pair_open is True
    assert result.chain_bound is True
    assert result.no_lookahead is True
    assert result.paper_only is True
    assert result.policy_actionable is False
    assert result.live_authorized is False

    assert result.incumbent_position.policy_source == "ML_CHAMPION"
    assert result.incumbent_position.model_id == INCUMBENT
    assert result.challenger_position.policy_source == "ML_CHALLENGER"
    assert result.challenger_position.model_id == CHALLENGER
    assert result.incumbent_position.entry_capital_quote == 1000.0
    assert result.challenger_position.entry_capital_quote == 1000.0
    assert result.incumbent_choice.decision_observed_at == DECISION
    assert result.challenger_choice.decision_observed_at == DECISION

    with storage.connect() as conn:
        cash = conn.execute(
            "SELECT cash_quote FROM paper_accounts WHERE account_id = ?",
            (ACCOUNT,),
        ).fetchone()
        previews = conn.execute(
            "SELECT COUNT(*) FROM pair_previews"
        ).fetchone()
    assert cash == (7990.0,)
    assert previews == (2,)


def test_second_binding_failure_rolls_back_first_position_and_cash(
    tmp_path,
    monkeypatch,
):
    storage = _seed(tmp_path / "pio.db")
    _install(monkeypatch, storage, fail_second_bind=True)

    with pytest.raises(ValueError, match="forced challenger bind failure"):
        _run(storage)

    with storage.connect() as conn:
        cash = conn.execute(
            "SELECT cash_quote FROM paper_accounts WHERE account_id = ?",
            (ACCOUNT,),
        ).fetchone()
        positions = conn.execute(
            "SELECT COUNT(*) FROM pair_positions"
        ).fetchone()
        previews = conn.execute(
            "SELECT COUNT(*) FROM pair_previews"
        ).fetchone()
    assert cash == (10000.0,)
    assert positions == (0,)
    assert previews == (0,)


def test_transaction_rechecks_cycle_model_binding_before_open(
    tmp_path,
    monkeypatch,
):
    storage = _seed(tmp_path / "pio.db")
    _install(monkeypatch, storage)
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE continuous_learning_cycles
            SET challenger_model_id = 'different-model'
            WHERE cycle_id = ?
            """,
            (CYCLE,),
        )
        conn.commit()

    with pytest.raises(ValueError, match="challenger/cycle binding"):
        _run(storage)

    with storage.connect() as conn:
        positions = conn.execute(
            "SELECT COUNT(*) FROM pair_positions"
        ).fetchone()
    assert positions == (0,)


def test_insufficient_cash_fails_before_candidate_scoring(
    tmp_path,
    monkeypatch,
):
    storage = _seed(tmp_path / "pio.db")
    frame_ids = _install(monkeypatch, storage)
    with storage.connect() as conn:
        conn.execute(
            "UPDATE paper_accounts SET cash_quote = 100.0 WHERE account_id = ?",
            (ACCOUNT,),
        )
        conn.commit()

    with pytest.raises(ValueError, match="insufficient"):
        _run(storage)

    assert frame_ids == []


def test_model_feature_version_drift_fails_closed(
    tmp_path,
    monkeypatch,
):
    storage = _seed(tmp_path / "pio.db")
    _install(monkeypatch, storage)
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE model_registry
            SET feature_version = 'OTHER'
            WHERE model_id = ?
            """,
            (CHALLENGER,),
        )
        conn.commit()

    with pytest.raises(ValueError, match="feature_version"):
        _run(storage)


def test_paired_entry_has_no_forward_label_or_live_execution_dependency():
    source = (
        ROOT
        / "python-learner"
        / "src"
        / "meteora_learner"
        / "paper_ml_pair_entry.py"
    ).read_text(encoding="utf-8")

    assert "target_net_return_bps" not in source
    assert "target_excess_vs_hold_bps" not in source
    assert "forward_observations" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert "paper_only=True" in source
    assert "live_authorized=False" in source
