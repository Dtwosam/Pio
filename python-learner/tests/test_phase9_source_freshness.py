from datetime import datetime, timedelta

from meteora_learner.contextual_bandit import (
    CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
)
from meteora_learner.mint_risk import MINT_RISK_EVIDENCE_TYPE
from meteora_learner.phase9_research import (
    PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
)
from meteora_learner.phase9_bandit_dataset import (
    PHASE9_BANDIT_DATASET_EVIDENCE_TYPE,
)
from meteora_learner.phase9_explicit_inputs import (
    parse_phase9_explicit_inputs,
    persist_phase9_explicit_inputs,
)
from meteora_learner.phase9_source_freshness import (
    evaluate_phase9_source_freshness,
)
from meteora_learner.portfolio_allocation import (
    PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
    PORTFOLIO_CANDIDATE_EVIDENCE_TYPE,
)
from meteora_learner.static_hedge import STATIC_HEDGE_EVIDENCE_TYPE
from meteora_learner.storage import Storage
from meteora_learner.wallet_flow import WALLET_FLOW_EVIDENCE_TYPE


def explicit_payload(*, hedge_cost=10.0, budget=200.0):
    return {
        "static_hedges": [
            {
                "pool_address": "pool-a",
                "amount_x": 100,
                "amount_y": 200,
                "instrument": {
                    "instrument_id": "SOL-PERP",
                    "venue": "TEST",
                    "available_liquidity_y_atomic": 1_000_000,
                    "max_liquidity_share_bps": 1000,
                    "max_leverage": 1.0,
                    "funding_bps_per_holding_window": 0.0,
                },
                "criteria": {
                    "observation_limit": 96,
                    "holding_observations": 6,
                    "hedge_fraction": 1.0,
                    "hedge_round_trip_cost_bps": hedge_cost,
                    "min_windows": 20,
                    "min_mean_abs_return_reduction_bps": 0.0,
                    "min_worst_loss_improvement_bps": 0.0,
                    "max_mean_return_drag_bps": 100.0,
                },
                "as_of": None,
            }
        ],
        "pool_inputs": [
            {
                "pool_address": pool,
                "amount_x": 100,
                "amount_y": 200,
                "requested_quote": 100.0,
                "network_cost_y_atomic": 1000,
            }
            for pool in ("pool-a", "pool-b", "pool-c")
        ],
        "portfolio": {
            "account_equity_quote": 1000.0,
            "cash_quote": 800.0,
            "current_deployed_quote": 200.0,
            "portfolio_drawdown_bps": 100,
            "observation_limit": 12,
            "half_widths": [0, 1, 2],
            "center_offsets": [0],
            "strategies": ["SPOT", "CURVE", "BID_ASK"],
            "max_share_bps": 500,
            "favor_x_in_active_bin": False,
            "budget_quote": budget,
            "allocation_criteria": {
                "max_positions": 3,
                "min_positions": 2,
                "max_pool_allocation_bps": 5000,
                "min_range_survival_ratio": 0.75,
                "min_excess_vs_hold_bps": 0,
                "min_position_quote": 10.0,
                "min_budget_utilization_rate": 0.75,
            },
        },
    }


def save_pool(storage, pool, observed_at):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": pool,
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": f"{pool}-x",
            "token_y_mint": f"{pool}-y",
            "bin_arrays": [],
        },
        observed_at=observed_at,
    )
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM chain_pool_snapshots
            WHERE pool_address = ?
              AND observed_at = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (pool, observed_at),
        ).fetchone()
    assert row is not None
    return int(row[0])


def save_mint(storage, mint, observed_at):
    return storage.save_token_mint_snapshot(
        {
            "mint_address": mint,
            "token_program": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "capture_slot_start": 1,
            "capture_slot_end": 2,
            "supply": "1000000",
            "decimals": 6,
            "is_initialized": True,
            "mint_authority": None,
            "freeze_authority": None,
            "data_len": 82,
            "token_2022_extension_data_len": 0,
            "has_token_2022_extension_data": False,
        },
        observed_at=observed_at,
    )


def save_wallet_event(storage, pool, suffix, created_at):
    with storage.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO position_event_history(
                observed_at, position_address, signature, ix_index,
                event_type, block_time, slot, pool_address,
                user_address, token_x, token_y,
                amount_x, amount_y, amount_x_usd, amount_y_usd,
                total_usd, created_at, raw_json
            ) VALUES (
                ?, ?, ?, 0, 'ADD_LIQUIDITY', 1, 1, ?,
                ?, 'X', 'Y', '1', '1', '50', '50',
                '100', ?, '{}'
            )
            """,
            (
                created_at,
                f"position-{suffix}",
                f"signature-{suffix}",
                pool,
                f"user-{suffix}",
                created_at,
            ),
        )
        return int(cursor.lastrowid)


def freshness(storage, family):
    report = evaluate_phase9_source_freshness(storage)
    return next(item for item in report.families if item.family == family)


def test_adaptive_freshness_detects_new_chain_snapshot(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    first = save_pool(storage, "pool-a", "2026-09-23T10:00:00+00:00")
    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
        as_of=None,
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "pools": [
                {
                    "pool_address": "pool-a",
                    "adaptive": {
                        "source_snapshot_ids": [first],
                    },
                }
            ],
        },
    )

    assert freshness(storage, "adaptive_regime").current is True

    save_pool(storage, "pool-a", "2026-09-23T11:00:00+00:00")
    item = freshness(storage, "adaptive_regime")
    assert item.current is False
    assert "chain history advanced" in item.reason


def test_mint_freshness_detects_new_mint_snapshot(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    pool_id = save_pool(
        storage,
        "pool-a",
        "2026-09-23T10:00:00+00:00",
    )
    mint_id = save_mint(
        storage,
        "pool-a-x",
        "2026-09-23T10:00:00+00:00",
    )
    storage.save_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T10:00:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "pool_snapshot_id": pool_id,
            "assessments": [
                {
                    "mint_address": "pool-a-x",
                    "mint_snapshot_id": mint_id,
                }
            ],
        },
    )

    assert freshness(storage, "mint_risk").current is True

    save_mint(
        storage,
        "pool-a-x",
        "2026-09-23T11:00:00+00:00",
    )
    item = freshness(storage, "mint_risk")
    assert item.current is False
    assert "source snapshot advanced" in item.reason


def test_wallet_freshness_detects_new_event(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    first = save_wallet_event(
        storage,
        "pool-a",
        "one",
        "2026-09-23T10:00:00+00:00",
    )
    storage.save_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of=None,
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "source_event_ids": [first],
        },
    )

    assert freshness(storage, "wallet_flow").current is True

    save_wallet_event(
        storage,
        "pool-a",
        "two",
        "2026-09-23T11:00:00+00:00",
    )
    item = freshness(storage, "wallet_flow")
    assert item.current is False
    assert "wallet-flow history advanced" in item.reason


def test_operational_freshness_requires_ranked_mint_and_wallet_pools(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    pool_a_id = save_pool(
        storage,
        "pool-a",
        "2026-09-23T10:00:00+00:00",
    )
    save_pool(
        storage,
        "pool-b",
        "2026-09-23T10:00:00+00:00",
    )
    mint_a_id = save_mint(
        storage,
        "pool-a-x",
        "2026-09-23T10:00:00+00:00",
    )
    event_a_id = save_wallet_event(
        storage,
        "pool-a",
        "one",
        "2026-09-23T10:00:00+00:00",
    )
    with storage.connect() as conn:
        for pool, tvl in (
            ("pool-b", 2_000.0),
            ("pool-a", 1_000.0),
        ):
            conn.execute(
                """
                INSERT INTO pool_snapshots(
                    observed_at, address, name, tvl,
                    volume_24h, fees_24h, raw_json
                ) VALUES (
                    '2026-09-23T11:30:00+00:00',
                    ?, ?, ?, 100, 1, '{}'
                )
                """,
                (pool, pool, tvl),
            )

    storage.save_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T10:00:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "pool_snapshot_id": pool_a_id,
            "assessments": [
                {
                    "mint_address": "pool-a-x",
                    "mint_snapshot_id": mint_a_id,
                }
            ],
        },
    )
    storage.save_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of=None,
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "source_event_ids": [event_a_id],
        },
    )

    report = evaluate_phase9_source_freshness(
        storage,
        as_of="2026-09-23T12:00:00+00:00",
        required_mint_pools=1,
        required_wallet_pools=1,
    )
    by_family = {
        item.family: item
        for item in report.families
    }

    assert by_family["mint_risk"].current is False
    assert "pool-b" in by_family["mint_risk"].reason
    assert "ranked cohort" in by_family["mint_risk"].reason
    assert by_family["wallet_flow"].current is False
    assert "pool-b" in by_family["wallet_flow"].reason
    assert "ranked cohort" in by_family["wallet_flow"].reason


def test_static_hedge_freshness_detects_new_price_path_snapshot(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    first = save_pool(storage, "pool-a", "2026-09-23T10:00:00+00:00")
    storage.save_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of=None,
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "source_observations": [
                {
                    "pool_snapshot_id": first,
                    "observed_at": "2026-09-23T10:00:00+00:00",
                }
            ],
        },
    )

    assert freshness(storage, "static_hedge").current is True

    save_pool(storage, "pool-a", "2026-09-23T11:00:00+00:00")
    item = freshness(storage, "static_hedge")
    assert item.current is False
    assert "hedge price path advanced" in item.reason


def test_portfolio_freshness_detects_chain_history_after_candidate(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(storage, "pool-a", "2026-09-23T10:00:00+00:00")
    candidate_id = storage.save_advanced_edge_evidence(
        edge_type=PORTFOLIO_CANDIDATE_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO_CANDIDATES__",
        as_of=None,
        status="BUILT",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "source_inputs": [{"pool_address": "pool-a"}],
        },
    )
    storage.save_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
        as_of=None,
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "candidate_lineage": {
                "candidate_evidence_id": candidate_id,
                "candidate_evidence_sha256": "a" * 64,
            },
        },
    )

    assert freshness(storage, "portfolio_allocation").current is True

    with storage.connect() as conn:
        created_at = str(
            conn.execute(
                """
                SELECT created_at
                FROM advanced_edge_evidence
                WHERE id = ?
                """,
                (candidate_id,),
            ).fetchone()[0]
        )
    later = (
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        + timedelta(minutes=1)
    ).isoformat()
    save_pool(storage, "pool-a", later)

    item = freshness(storage, "portfolio_allocation")
    assert item.current is False
    assert "chain history advanced" in item.reason


def test_bandit_freshness_detects_new_retraining_dataset_cycle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status,
                metrics_json
            ) VALUES (
                'model', '2026-09-23T09:00:00+00:00',
                '2026-09-23T09:00:00+00:00',
                'TEST', 'TEST', 'dataset-v1', 'CHAMPION', '{}'
            )
            """
        )
    storage.save_model_live_evidence(
        model_id="model",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
        status="BUILT",
        evidence={"cycle_id": "cycle-1"},
    )
    storage.save_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
        as_of=None,
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "dataset_lineage": {
                "cycle_id": "cycle-1",
            },
        },
    )

    assert freshness(storage, "contextual_bandit").current is True

    storage.save_model_live_evidence(
        model_id="model",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
        status="BUILT",
        evidence={"cycle_id": "cycle-2"},
    )
    item = freshness(storage, "contextual_bandit")
    assert item.current is False
    assert "newer retraining dataset cycle" in item.reason


def test_adaptive_freshness_ignores_later_inserted_older_backfill(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    current_id = save_pool(
        storage,
        "pool-a",
        "2026-09-23T10:00:00+00:00",
    )
    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
        as_of=None,
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "pools": [
                {
                    "pool_address": "pool-a",
                    "adaptive": {
                        "source_snapshot_ids": [current_id],
                    },
                }
            ],
        },
    )

    # This row gets a newer database ID but represents older source time.
    save_pool(storage, "pool-a", "2026-09-23T09:00:00+00:00")

    item = freshness(storage, "adaptive_regime")
    assert item.current is True


def test_wallet_freshness_ignores_later_inserted_older_backfill(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    current_id = save_wallet_event(
        storage,
        "pool-a",
        "current",
        "2026-09-23T10:00:00+00:00",
    )
    storage.save_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of=None,
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "source_event_ids": [current_id],
        },
    )

    save_wallet_event(
        storage,
        "pool-a",
        "historical",
        "2026-09-23T09:00:00+00:00",
    )

    item = freshness(storage, "wallet_flow")
    assert item.current is True


def test_portfolio_freshness_prefers_explicit_chain_watermarks(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    first = save_pool(
        storage,
        "pool-a",
        "2026-09-23T10:00:00+00:00",
    )
    candidate_id = storage.save_advanced_edge_evidence(
        edge_type=PORTFOLIO_CANDIDATE_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO_CANDIDATES__",
        as_of=None,
        status="BUILT",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "source_inputs": [{"pool_address": "pool-a"}],
            "assumptions": {
                "source_chain_watermarks": [
                    {
                        "pool_address": "pool-a",
                        "snapshot_id": first,
                        "observed_at": "2026-09-23T10:00:00+00:00",
                    }
                ],
            },
        },
    )
    storage.save_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
        as_of=None,
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "candidate_lineage": {
                "candidate_evidence_id": candidate_id,
                "candidate_evidence_sha256": "a" * 64,
            },
        },
    )

    assert freshness(storage, "portfolio_allocation").current is True

    save_pool(
        storage,
        "pool-a",
        "2026-09-23T11:00:00+00:00",
    )
    item = freshness(storage, "portfolio_allocation")
    assert item.current is False
    assert "chain history advanced from" in item.reason


def test_adaptive_freshness_detects_ranked_research_cohort_change(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        for rank, pool in enumerate(
            ("pool-a", "pool-b", "pool-c", "pool-d"),
        ):
            conn.execute(
                """
                INSERT INTO pool_snapshots(
                    observed_at, address, name, tvl,
                    volume_24h, fees_24h, raw_json
                ) VALUES (
                    '2026-09-23T12:00:00+00:00',
                    ?, ?, ?, 100, 1, '{}'
                )
                """,
                (pool, pool, 1_000 - rank * 100),
            )

    latest_ids = {}
    for pool in ("pool-a", "pool-b", "pool-c"):
        for minute in range(43):
            latest_ids[pool] = save_pool(
                storage,
                pool,
                f"2026-09-23T10:{minute:02d}:00+00:00",
            )
    for minute in range(42):
        save_pool(
            storage,
            "pool-d",
            f"2026-09-23T10:{minute:02d}:30+00:00",
        )

    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
        as_of=None,
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "pools": [
                {
                    "pool_address": pool,
                    "adaptive": {
                        "source_snapshot_ids": [latest_ids[pool]],
                    },
                }
                for pool in ("pool-a", "pool-b", "pool-c")
            ],
        },
    )

    assert freshness(storage, "adaptive_regime").current is True

    save_pool(
        storage,
        "pool-d",
        "2026-09-23T10:42:30+00:00",
    )

    item = freshness(storage, "adaptive_regime")
    assert item.current is False
    assert "ranked research cohort changed" in item.reason
    assert "pool-d" in item.reason


def test_static_hedge_freshness_detects_new_explicit_assumptions(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    first = save_pool(
        storage,
        "pool-a",
        "2026-09-23T10:00:00+00:00",
    )
    artifact_a = persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(explicit_payload()),
    )
    spec = artifact_a.inputs.static_hedges[0]
    storage.save_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of=None,
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "amount_x": spec.amount_x,
            "amount_y": spec.amount_y,
            "instrument": {
                "instrument_id": spec.instrument.instrument_id,
                "venue": spec.instrument.venue,
                "available_liquidity_y_atomic": (
                    spec.instrument.available_liquidity_y_atomic
                ),
                "max_liquidity_share_bps": (
                    spec.instrument.max_liquidity_share_bps
                ),
                "max_leverage": spec.instrument.max_leverage,
                "funding_bps_per_holding_window": (
                    spec.instrument.funding_bps_per_holding_window
                ),
            },
            "criteria": {
                "observation_limit": spec.criteria.observation_limit,
                "holding_observations": spec.criteria.holding_observations,
                "hedge_fraction": spec.criteria.hedge_fraction,
                "hedge_round_trip_cost_bps": (
                    spec.criteria.hedge_round_trip_cost_bps
                ),
                "min_windows": spec.criteria.min_windows,
                "min_mean_abs_return_reduction_bps": (
                    spec.criteria.min_mean_abs_return_reduction_bps
                ),
                "min_worst_loss_improvement_bps": (
                    spec.criteria.min_worst_loss_improvement_bps
                ),
                "max_mean_return_drag_bps": (
                    spec.criteria.max_mean_return_drag_bps
                ),
            },
            "as_of": spec.as_of,
            "source_observations": [
                {
                    "pool_snapshot_id": first,
                    "observed_at": "2026-09-23T10:00:00+00:00",
                }
            ],
        },
    )

    assert freshness(storage, "static_hedge").current is True

    persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(
            explicit_payload(hedge_cost=25.0)
        ),
    )
    item = freshness(storage, "static_hedge")
    assert item.current is False
    assert "assumptions no longer match" in item.reason


def test_portfolio_freshness_detects_new_explicit_assumptions(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_pool(
            storage,
            pool,
            "2026-09-23T10:00:00+00:00",
        )
    artifact_a = persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(explicit_payload()),
    )
    candidate_id = storage.save_advanced_edge_evidence(
        edge_type=PORTFOLIO_CANDIDATE_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO_CANDIDATES__",
        as_of=None,
        status="BUILT",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "source_inputs": [
                {
                    "pool_address": pool,
                    "amount_x": 100,
                    "amount_y": 200,
                    "requested_quote": 100.0,
                    "network_cost_y_atomic": 1000,
                }
                for pool in ("pool-a", "pool-b", "pool-c")
            ],
            "assumptions": {
                "explicit_input_evidence_id": artifact_a.evidence_id,
                "explicit_input_artifact_sha256": (
                    artifact_a.artifact_sha256
                ),
            },
        },
    )
    storage.save_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
        as_of=None,
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "candidate_lineage": {
                "candidate_evidence_id": candidate_id,
                "candidate_evidence_sha256": "a" * 64,
            },
        },
    )

    assert freshness(storage, "portfolio_allocation").current is True

    artifact_b = persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(
            explicit_payload(budget=300.0)
        ),
    )
    item = freshness(storage, "portfolio_allocation")
    assert item.current is False
    assert str(artifact_a.evidence_id) in item.reason
    assert str(artifact_b.evidence_id) in item.reason


def test_phase9_bandit_freshness_detects_new_explicit_assumptions(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_pool(
            storage,
            pool,
            "2026-09-23T10:00:00+00:00",
        )
    artifact_a = persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(explicit_payload()),
    )
    storage.save_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
        as_of="2026-09-23T10:00:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "dataset_lineage": {
                "source_type": PHASE9_BANDIT_DATASET_EVIDENCE_TYPE,
                "explicit_input_evidence_id": artifact_a.evidence_id,
                "explicit_input_artifact_sha256": (
                    artifact_a.artifact_sha256
                ),
                "cutoff": "2026-09-23T10:00:00+00:00",
            },
        },
    )

    assert freshness(storage, "contextual_bandit").current is True

    artifact_b = persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(
            explicit_payload(budget=350.0)
        ),
    )
    item = freshness(storage, "contextual_bandit")
    assert item.current is False
    assert str(artifact_a.evidence_id) in item.reason
    assert str(artifact_b.evidence_id) in item.reason
