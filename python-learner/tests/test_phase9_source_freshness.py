from datetime import datetime, timedelta

from meteora_learner.contextual_bandit import (
    CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
)
from meteora_learner.mint_risk import MINT_RISK_EVIDENCE_TYPE
from meteora_learner.phase9_research import (
    PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
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
