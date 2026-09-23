import hashlib
from pathlib import Path
from dataclasses import replace

from meteora_learner.chain_snapshot_lineage import (
    chain_snapshot_source_record,
    chain_snapshot_source_sha256,
)
from meteora_learner.contextual_bandit import (
    CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
)
from meteora_learner.mint_risk import (
    MINT_RISK_EVIDENCE_TYPE,
    persist_pool_mint_risk,
    research_pool_mint_risk,
)
from meteora_learner.phase9_research import (
    PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
)
from meteora_learner.phase9_validation import (
    PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE,
    Phase9ResearchBundleCriteria,
    evaluate_phase9_promotion,
    evaluate_phase9_research_bundle,
    persist_phase9_research_bundle,
    phase9_research_bundle_sha256,
)
from meteora_learner.phase_promotion import (
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
    PHASE9,
    persist_phase9_promotion,
    phase_promotion_state,
)
from meteora_learner.portfolio_allocation import (
    PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
    portfolio_candidate_artifact_sha256,
)
from meteora_learner.static_hedge import (
    STATIC_HEDGE_EVIDENCE_TYPE,
    StaticHedgeSourceObservation,
    static_hedge_source_sha256,
)
from meteora_learner.storage import Storage
from meteora_learner.wallet_flow import (
    WALLET_FLOW_EVIDENCE_TYPE,
    WalletFlowCriteria,
    persist_wallet_flow_research,
    research_wallet_flow,
    wallet_flow_source_sha256,
)


def promote_phase8(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def seed_portfolio_candidate_lineage(storage):
    payload = {
        "research_only": True,
        "policy_actionable": False,
        "source_inputs": [{"pool_address": "pool-a"}],
        "assumptions": {"budget_context": "test"},
        "comparison": {"candidates": [{"pool_address": "pool-a"}]},
    }
    artifact_sha = portfolio_candidate_artifact_sha256(payload)
    evidence_id = storage.save_advanced_edge_evidence(
        edge_type="PHASE9_PORTFOLIO_CANDIDATES_V1",
        pool_address="__PORTFOLIO_CANDIDATES__",
        as_of="2026-09-23T12:00:00+00:00",
        status="BUILT",
        qualified=True,
        evidence={
            "artifact_sha256": artifact_sha,
            **payload,
        },
    )
    return {
        "candidate_evidence_id": evidence_id,
        "candidate_evidence_sha256": artifact_sha,
    }


def seed_bandit_dataset_lineage(storage):
    dataset_path = Path(storage.path).parent / "phase9-bandit.csv"
    dataset_path.write_text(
        "decision_observed_at,forward_end_observed_at\n"
        "2026-09-23T10:00:00+00:00,2026-09-23T11:00:00+00:00\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    dataset_version = f"ML_ACTION_DATASET_V1:{digest[:16]}"
    cutoff = "2026-09-23T12:00:00+00:00"

    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status,
                metrics_json
            ) VALUES (
                'champion', '2026-09-23T00:00:00+00:00',
                '2026-09-23T00:00:00+00:00',
                'TEST', 'TEST', 'dataset-v1', 'CHAMPION', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO continuous_learning_cycles(
                cycle_id, created_at, updated_at, status, active_key,
                champion_model_id, champion_dataset_version,
                champion_evidence_watermark, plan_evidence_id,
                plan_as_of, target_dataset_version,
                challenger_model_id, plan_json, notes
            ) VALUES (
                'cycle', '2026-09-23T00:00:00+00:00',
                '2026-09-23T00:00:00+00:00',
                'PLANNED', NULL, 'champion', 'dataset-v1',
                '2026-09-23T00:00:00+00:00', 1,
                ?, ?, NULL, '{}', NULL
            )
            """,
            (cutoff, dataset_version),
        )
    evidence_id = storage.save_model_live_evidence(
        model_id="champion",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
        status="BUILT",
        evidence={
            "cycle_id": "cycle",
            "cutoff": cutoff,
            "target_dataset_version": dataset_version,
            "dataset": {
                "dataset_sha256": digest,
                "dataset_version": dataset_version,
            },
            "output_file": str(dataset_path),
        },
    )
    return {
        "cycle_id": "cycle",
        "champion_model_id": "champion",
        "dataset_evidence_id": evidence_id,
        "dataset_version": dataset_version,
        "dataset_sha256": digest,
        "cutoff": cutoff,
        "output_file": str(dataset_path),
    }


def evidence(
    storage,
    edge_type,
    pool,
    *,
    qualified=True,
    research_only=True,
    policy_actionable=False,
    extra=None,
):
    return storage.save_advanced_edge_evidence(
        edge_type=edge_type,
        pool_address=pool,
        as_of="2026-09-23T12:00:00+00:00",
        status=(
            "QUALIFIED_RESEARCH"
            if qualified
            else "NOT_QUALIFIED"
        ),
        qualified=qualified,
        evidence={
            "research_qualified": qualified,
            "research_only": research_only,
            "policy_actionable": policy_actionable,
            **(extra or {}),
        },
    )


def seed_wallet_flow_lineage(storage, pool):
    created_at = "2026-09-23T12:00:00+00:00"
    signature = f"sig-{pool}"
    position = f"position-{pool}"
    user = f"user-{pool}"
    with storage.connect() as conn:
        conn.execute(
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
                position,
                signature,
                pool,
                user,
                created_at,
            ),
        )

    report = research_wallet_flow(
        storage,
        pool_address=pool,
        criteria=WalletFlowCriteria(
            lookback_events=10,
            min_events=1,
            min_unique_users=1,
            max_top_user_share_bps=10_000,
        ),
        as_of=created_at,
    )
    assert report.research_qualified is True
    persist_wallet_flow_research(storage, report=report)


def seed_mint_risk_lineage(storage, pool):
    observed_at = "2026-09-23T12:00:00+00:00"
    token_program = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    x = f"{pool}-x"
    y = f"{pool}-y"
    with storage.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO chain_pool_snapshots(
                observed_at, pool_address, active_bin_id, bin_step,
                token_x_mint, token_y_mint,
                token_x_program, token_y_program, raw_json
            ) VALUES (?, ?, 0, 25, ?, ?, ?, ?, '{}')
            """,
            (observed_at, pool, x, y, token_program, token_program),
        )
        pool_snapshot_id = int(cursor.lastrowid)
        pool_source = conn.execute(
            f"""
            SELECT {", ".join(POOL_SOURCE_COLUMNS)}
            FROM chain_pool_snapshots
            WHERE id = ?
            """,
            (pool_snapshot_id,),
        ).fetchone()
        assert pool_source is not None
        pool_snapshot_sha256 = mint_risk_source_sha256(
            mint_risk_pool_source_record(pool_source)
        )

    assessment_rows = []
    for mint, role in ((x, "TOKEN_X"), (y, "TOKEN_Y")):
        storage.save_token_mint_snapshot(
            {
                "mint_address": mint,
                "token_program": token_program,
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
        with storage.connect() as conn:
            mint_source = conn.execute(
                f"""
                SELECT {", ".join(MINT_SOURCE_COLUMNS)}
                FROM token_mint_snapshots
                WHERE mint_address = ? AND observed_at = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (mint, observed_at),
            ).fetchone()
            assert mint_source is not None
        mint_record = mint_risk_mint_source_record(mint_source)
        assessment_rows.append(
            {
                "mint_address": mint,
                "roles": [role],
                "mint_snapshot_id": int(mint_record["id"]),
                "mint_snapshot_sha256": mint_risk_source_sha256(
                    mint_record
                ),
                "observed_at": observed_at,
                "accepted": True,
            }
        )

    evidence(
        storage,
        MINT_RISK_EVIDENCE_TYPE,
        pool,
        extra={
            "pool_snapshot_id": pool_snapshot_id,
            "pool_snapshot_sha256": pool_snapshot_sha256,
            "assessments": assessment_rows,
        },
    )


def seed_adaptive_multi_pool_lineage(storage, pools):
    pool_records = []
    with storage.connect() as conn:
        for pool in pools:
            row = conn.execute(
                """
                SELECT id, pool_address, observed_at, active_bin_id
                FROM chain_pool_snapshots
                WHERE pool_address = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (pool,),
            ).fetchone()
            assert row is not None
            record = chain_snapshot_source_record(row)
            source_ids = [int(record["id"])]
            source_sha = chain_snapshot_source_sha256([record])
            observed_at = str(record["observed_at"])
            pool_records.append(
                {
                    "pool_address": pool,
                    "adaptive": {
                        "as_of": observed_at,
                        "source_snapshot_ids": source_ids,
                        "source_snapshot_sha256": source_sha,
                    },
                    "regime": {
                        "as_of": observed_at,
                        "source_snapshot_ids": source_ids,
                        "source_snapshot_sha256": source_sha,
                    },
                }
            )

    evidence(
        storage,
        PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        "__MULTI_POOL__",
        extra={"pools": pool_records},
    )

def seed_static_hedge_lineage(storage, pool):
    with storage.connect() as conn:
        pool_row = conn.execute(
            """
            SELECT id, observed_at, active_bin_id
            FROM chain_pool_snapshots
            WHERE pool_address = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (pool,),
        ).fetchone()
        assert pool_row is not None
        pool_snapshot_id = int(pool_row[0])
        observed_at = str(pool_row[1])
        active_bin_id = int(pool_row[2])
        cursor = conn.execute(
            """
            INSERT INTO bin_liquidity_snapshots(
                observed_at, pool_address, bin_array_index,
                bin_id, price, amount_x, amount_y,
                liquidity_supply, fee_amount_x_per_token_stored,
                fee_amount_y_per_token_stored
            ) VALUES (?, ?, 0, ?, ?, '1', '1', '1', '0', '0')
            """,
            (
                observed_at,
                pool,
                active_bin_id,
                str(1 << 64),
            ),
        )
        bin_snapshot_id = int(cursor.lastrowid)

    observation = StaticHedgeSourceObservation(
        pool_snapshot_id=pool_snapshot_id,
        bin_liquidity_snapshot_id=bin_snapshot_id,
        pool_address=pool,
        observed_at=observed_at,
        active_bin_id=active_bin_id,
        price_q64=1 << 64,
    )
    evidence(
        storage,
        STATIC_HEDGE_EVIDENCE_TYPE,
        pool,
        extra={
            "as_of": observed_at,
            "source_observations": [
                {
                    "pool_snapshot_id": observation.pool_snapshot_id,
                    "bin_liquidity_snapshot_id": (
                        observation.bin_liquidity_snapshot_id
                    ),
                    "pool_address": observation.pool_address,
                    "observed_at": observation.observed_at,
                    "active_bin_id": observation.active_bin_id,
                    "price_q64": observation.price_q64,
                }
            ],
            "source_path_sha256": static_hedge_source_sha256(
                [observation]
            ),
        },
    )

def seed_ready(storage):
    promote_phase8(storage)
    portfolio_lineage = seed_portfolio_candidate_lineage(storage)
    bandit_lineage = seed_bandit_dataset_lineage(storage)
    for pool in ("pool-a", "pool-b"):
        seed_mint_risk_lineage(storage, pool)
        seed_wallet_flow_lineage(storage, pool)
    seed_adaptive_multi_pool_lineage(
        storage,
        ("pool-a", "pool-b"),
    )
    evidence(
        storage,
        PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        "__PORTFOLIO__",
        extra={"candidate_lineage": portfolio_lineage},
    )
    seed_static_hedge_lineage(storage, "pool-a")
    evidence(
        storage,
        CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        "__CONTEXTUAL_BANDIT__",
        extra={"dataset_lineage": bandit_lineage},
    )


def test_phase9_bundle_ready_with_complete_research_corpus(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is True
    assert report.status == "RESEARCH_BUNDLE_READY"
    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.mint_risk.qualified_records == 2
    assert report.wallet_flow.qualified_records == 2

    evidence_id = persist_phase9_research_bundle(
        storage,
        report=report,
    )
    assert evidence_id > 0
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE,
        pool_address="__PHASE9_RESEARCH__",
    )
    assert latest is not None
    assert latest["qualified"] is True
    assert latest["evidence"]["policy_actionable"] is False


def test_missing_research_family_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            DELETE FROM advanced_edge_evidence
            WHERE edge_type = ?
            """,
            (CONTEXTUAL_BANDIT_EVIDENCE_TYPE,),
        )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert report.status == "RESEARCH_BUNDLE_INCOMPLETE"
    assert any(
        "contextual-bandit" in reason
        for reason in report.reasons
    )


def test_latest_boundary_violation_invalidates_old_qualified_evidence(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    evidence(
        storage,
        MINT_RISK_EVIDENCE_TYPE,
        "pool-a",
        qualified=True,
        research_only=False,
        policy_actionable=True,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert report.mint_risk.boundary_valid is False
    assert any(
        "mint risk evidence violates" in reason
        for reason in report.reasons
    )


def test_phase8_promotion_is_required(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    evidence(
        storage,
        PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        "__MULTI_POOL__",
    )
    for pool in ("pool-a", "pool-b"):
        evidence(storage, MINT_RISK_EVIDENCE_TYPE, pool)
        evidence(storage, WALLET_FLOW_EVIDENCE_TYPE, pool)
    evidence(
        storage,
        PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        "__PORTFOLIO__",
    )
    evidence(storage, STATIC_HEDGE_EVIDENCE_TYPE, "pool-a")
    evidence(
        storage,
        CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        "__CONTEXTUAL_BANDIT__",
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert report.status == "RESEARCH_ONLY_PHASE8_BLOCKED"


def test_bundle_criteria_can_require_more_pool_diversity(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)

    report = evaluate_phase9_research_bundle(
        storage,
        criteria=Phase9ResearchBundleCriteria(
            min_mint_risk_pools=3,
            min_wallet_flow_pools=3,
            min_static_hedge_pools=2,
        ),
    )

    assert report.research_ready is False
    assert any(
        "mint-risk pools 2 are below 3" in reason
        for reason in report.reasons
    )
    assert any(
        "wallet-flow pools 2 are below 3" in reason
        for reason in report.reasons
    )
    assert any(
        "static-hedge pools 1 are below 2" in reason
        for reason in report.reasons
    )


def test_phase9_promotion_persists_non_actionable_ready_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)

    bundle = evaluate_phase9_research_bundle(storage)
    bundle_id = persist_phase9_research_bundle(
        storage,
        report=bundle,
    )
    report = evaluate_phase9_promotion(storage)

    assert report.promotion_ready is True
    assert report.research_bundle_evidence_id == bundle_id
    assert report.persisted_bundle_hash_valid is True
    assert report.persisted_bundle_matches_current is True
    assert report.persisted_bundle_sha256 == report.research_bundle_sha256
    assert report.research_only is True
    assert report.policy_actionable is False

    state = persist_phase9_promotion(
        storage,
        report=report,
    )
    assert state.phase_name == PHASE9
    assert state.promoted is True
    assert phase_promotion_state(
        storage,
        phase_name=PHASE9,
    ).promoted is True



def test_phase9_promotion_rejects_tampered_bundle_payload(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    bundle_id = persist_phase9_research_bundle(
        storage,
        report=bundle,
    )

    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE,
        pool_address="__PHASE9_RESEARCH__",
    )
    assert latest is not None
    assert latest["id"] == bundle_id
    persisted = dict(latest["evidence"])
    expected_sha = persisted["bundle_sha256"]
    payload = {
        key: value
        for key, value in persisted.items()
        if key != "bundle_sha256"
    }
    assert phase9_research_bundle_sha256(payload) == expected_sha

    import json

    payload["status"] = "TAMPERED_READY"
    tampered = {
        **payload,
        "bundle_sha256": expected_sha,
    }
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE advanced_edge_evidence
            SET evidence_json = ?
            WHERE id = ?
            """,
            (json.dumps(tampered, sort_keys=True), bundle_id),
        )

    report = evaluate_phase9_promotion(storage)

    assert report.promotion_ready is False
    assert report.persisted_bundle_hash_valid is False
    assert any(
        "checksum is invalid" in reason
        for reason in report.reasons
    )

def test_phase9_promotion_requires_complete_research_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            DELETE FROM advanced_edge_evidence
            WHERE edge_type = ?
            """,
            (STATIC_HEDGE_EVIDENCE_TYPE,),
        )

    report = evaluate_phase9_promotion(storage)

    assert report.promotion_ready is False
    assert report.policy_actionable is False
    assert any(
        "static-hedge" in reason
        for reason in report.reasons
    )


def test_phase9_promotion_rejects_stale_persisted_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(
        storage,
        report=bundle,
    )

    seed_mint_risk_lineage(storage, "pool-c")

    report = evaluate_phase9_promotion(storage)

    assert report.promotion_ready is False
    assert report.persisted_bundle_matches_current is False
    assert any(
        "stale versus current evidence" in reason
        for reason in report.reasons
    )


def test_phase9_promotion_refuses_live_policy_authority(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(
        storage,
        report=bundle,
    )
    report = evaluate_phase9_promotion(storage)
    assert report.promotion_ready is True

    actionable = replace(
        report,
        policy_actionable=True,
    )
    try:
        persist_phase9_promotion(
            storage,
            report=actionable,
        )
    except ValueError as exc:
        assert "must not grant live-policy authority" in str(exc)
    else:
        raise AssertionError(
            "expected actionable Phase 9 promotion refusal"
        )

    not_research_only = replace(
        report,
        research_only=False,
    )
    try:
        persist_phase9_promotion(
            storage,
            report=not_research_only,
        )
    except ValueError as exc:
        assert "must remain research-only" in str(exc)
    else:
        raise AssertionError(
            "expected non-research Phase 9 promotion refusal"
        )


def test_forged_bandit_dataset_lineage_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["dataset_lineage"] = {
        **forged["dataset_lineage"],
        "dataset_evidence_id": 999999,
    }
    storage.save_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
        as_of="2026-09-23T12:01:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "checksum-verified retraining dataset lineage" in reason
        for reason in report.reasons
    )


def test_forged_portfolio_candidate_lineage_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["candidate_lineage"] = {
        **forged["candidate_lineage"],
        "candidate_evidence_id": 999999,
    }
    storage.save_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
        as_of="2026-09-23T12:02:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "immutable candidate-artifact lineage" in reason
        for reason in report.reasons
    )


def test_forged_mint_snapshot_lineage_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["assessments"] = [
        {
            **item,
            "mint_snapshot_id": 999999,
        }
        for item in forged["assessments"]
    ]
    storage.save_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:03:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "authoritative pool and mint snapshot IDs" in reason
        for reason in report.reasons
    )



def test_tampered_mint_snapshot_payload_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    mint_id = int(
        latest["evidence"]["assessments"][0]["mint_snapshot_id"]
    )

    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE token_mint_snapshots
            SET mint_authority = 'tampered-authority'
            WHERE id = ?
            """,
            (mint_id,),
        )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "authoritative pool and mint snapshot IDs" in reason
        for reason in report.reasons
    )


def test_tampered_pool_snapshot_payload_blocks_mint_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    pool_snapshot_id = int(
        latest["evidence"]["pool_snapshot_id"]
    )

    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE chain_pool_snapshots
            SET bin_step = bin_step + 1
            WHERE id = ?
            """,
            (pool_snapshot_id,),
        )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "authoritative pool and mint snapshot IDs" in reason
        for reason in report.reasons
    )

def test_forged_wallet_flow_lineage_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["source_event_sha256"] = "0" * 64
    storage.save_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:04:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "immutable source event IDs and SHA-256" in reason
        for reason in report.reasons
    )


def test_forged_adaptive_snapshot_lineage_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    pools = [dict(item) for item in forged["pools"]]
    pools[0] = {
        **pools[0],
        "adaptive": {
            **pools[0]["adaptive"],
            "source_snapshot_ids": [999999],
        },
    }
    forged["pools"] = pools
    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
        as_of="2026-09-23T12:04:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "immutable chain snapshot IDs" in reason
        for reason in report.reasons
    )


def test_forged_static_hedge_lineage_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    observations = [
        dict(item) for item in forged["source_observations"]
    ]
    observations[0]["bin_liquidity_snapshot_id"] = 999999
    forged["source_observations"] = observations
    storage.save_advanced_edge_evidence(
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:08:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "pool/bin price-path IDs" in reason
        for reason in report.reasons
    )


def test_tampered_portfolio_candidate_payload_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest_allocation = storage.latest_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
    )
    assert latest_allocation is not None
    lineage = latest_allocation["evidence"]["candidate_lineage"]
    evidence_id = int(lineage["candidate_evidence_id"])

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT evidence_json
            FROM advanced_edge_evidence
            WHERE id = ?
            """,
            (evidence_id,),
        ).fetchone()
        assert row is not None
        import json

        payload = json.loads(str(row[0]))
        payload["source_inputs"] = [{"pool_address": "tampered"}]
        conn.execute(
            """
            UPDATE advanced_edge_evidence
            SET evidence_json = ?
            WHERE id = ?
            """,
            (json.dumps(payload, sort_keys=True), evidence_id),
        )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "immutable candidate-artifact lineage" in reason
        for reason in report.reasons
    )


def test_tampered_bandit_dataset_file_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
    )
    assert latest is not None
    lineage = latest["evidence"]["dataset_lineage"]
    dataset_path = Path(str(lineage["output_file"]))
    assert dataset_path.is_file()

    dataset_path.write_text(
        dataset_path.read_text(encoding="utf-8")
        + "2026-09-23T11:00:00+00:00,"
        "2026-09-23T11:30:00+00:00\n",
        encoding="utf-8",
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "checksum-verified retraining dataset lineage" in reason
        for reason in report.reasons
    )


def test_forged_mint_assessment_facts_block_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    assessments = [
        dict(item) for item in forged["assessments"]
    ]
    assessments[0]["decimals"] = int(assessments[0]["decimals"]) + 1
    forged["assessments"] = assessments
    storage.save_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:10:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "authoritative pool and mint snapshot IDs" in reason
        for reason in report.reasons
    )


def test_forged_wallet_flow_metric_blocks_bundle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    assert latest is not None
    forged = dict(latest["evidence"])
    forged["total_activity_usd"] = (
        float(forged["total_activity_usd"]) + 1.0
    )
    storage.save_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:11:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    report = evaluate_phase9_research_bundle(storage)

    assert report.research_ready is False
    assert any(
        "immutable position-event IDs" in reason
        for reason in report.reasons
    )
