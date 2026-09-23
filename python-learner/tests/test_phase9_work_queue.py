from meteora_learner.chain_snapshot_lineage import (
    chain_snapshot_source_record,
    chain_snapshot_source_sha256,
)
from meteora_learner.contextual_bandit import (
    CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
)
from meteora_learner.mint_risk import MINT_RISK_EVIDENCE_TYPE
from meteora_learner.phase9_research import (
    PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
)
from meteora_learner.phase9_validation import (
    Phase9ResearchBundleCriteria,
    evaluate_phase9_research_bundle,
    persist_phase9_research_bundle,
)
from meteora_learner.phase9_work_queue import build_phase9_work_queue
from meteora_learner.phase_promotion import (
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
)
from meteora_learner.portfolio_allocation import (
    PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
)
from meteora_learner.static_hedge import STATIC_HEDGE_EVIDENCE_TYPE
from meteora_learner.storage import Storage
from meteora_learner.wallet_flow import (
    WALLET_FLOW_EVIDENCE_TYPE,
    wallet_flow_source_sha256,
)


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


def save_mint_snapshot(storage, mint):
    storage.save_token_mint_snapshot(
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
        observed_at="2026-09-23T12:00:00+00:00",
    )


def seed_portfolio_candidate_lineage(storage):
    artifact_sha = "portfolio-deadbeef"
    evidence_id = storage.save_advanced_edge_evidence(
        edge_type="PHASE9_PORTFOLIO_CANDIDATES_V1",
        pool_address="__PORTFOLIO_CANDIDATES__",
        as_of="2026-09-23T12:00:00+00:00",
        status="BUILT",
        qualified=True,
        evidence={
            "artifact_sha256": artifact_sha,
            "research_only": True,
            "policy_actionable": False,
            "source_inputs": [{"pool_address": "pool-a"}],
            "assumptions": {"budget_context": "test"},
            "comparison": {"candidates": [{"pool_address": "pool-a"}]},
        },
    )
    return {
        "candidate_evidence_id": evidence_id,
        "candidate_evidence_sha256": artifact_sha,
    }


def seed_retraining_dataset_evidence(
    storage,
    *,
    cycle_id="cycle-lineage",
    dataset_version="ML_ACTION_DATASET_V1:deadbeefdeadbeef",
    dataset_sha256="deadbeefdeadbeefdeadbeef",
    cutoff="2026-09-23T12:00:00+00:00",
    output_file="retrain.csv",
):
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
                ?, '2026-09-23T00:00:00+00:00',
                '2026-09-23T00:00:00+00:00',
                'PLANNED', NULL, 'champion', 'dataset-v1',
                '2026-09-23T00:00:00+00:00', 1,
                ?, ?, NULL, '{}', NULL
            )
            """,
            (cycle_id, cutoff, dataset_version),
        )
    evidence_id = storage.save_model_live_evidence(
        model_id="champion",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
        status="BUILT",
        evidence={
            "cycle_id": cycle_id,
            "cutoff": cutoff,
            "target_dataset_version": dataset_version,
            "dataset": {
                "dataset_sha256": dataset_sha256,
                "dataset_version": dataset_version,
            },
            "output_file": output_file,
        },
    )
    return {
        "cycle_id": cycle_id,
        "champion_model_id": "champion",
        "dataset_evidence_id": evidence_id,
        "dataset_version": dataset_version,
        "dataset_sha256": dataset_sha256,
        "cutoff": cutoff,
        "output_file": output_file,
    }


def promote_phase8(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def evidence(storage, edge_type, pool, *, extra=None):
    return storage.save_advanced_edge_evidence(
        edge_type=edge_type,
        pool_address=pool,
        as_of="2026-09-23T12:00:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_qualified": True,
            "research_only": True,
            "policy_actionable": False,
            **(extra or {}),
        },
    )


def seed_wallet_flow_lineage(storage, pool):
    created_at = "2026-09-23T12:00:00+00:00"
    signature = f"sig-{pool}"
    position = f"position-{pool}"
    user = f"user-{pool}"
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
                position,
                signature,
                pool,
                user,
                created_at,
            ),
        )
        event_id = int(cursor.lastrowid)

    source_records = [
        {
            "id": event_id,
            "created_at": created_at,
            "user_address": user,
            "event_type": "ADD_LIQUIDITY",
            "total_usd": "100",
            "signature": signature,
            "ix_index": 0,
            "position_address": position,
        }
    ]
    evidence(
        storage,
        WALLET_FLOW_EVIDENCE_TYPE,
        pool,
        extra={
            "source_event_ids": [event_id],
            "source_event_sha256": wallet_flow_source_sha256(
                source_records
            ),
        },
    )


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
            snapshot_id = int(
                conn.execute(
                    """
                    SELECT id
                    FROM token_mint_snapshots
                    WHERE mint_address = ? AND observed_at = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (mint, observed_at),
                ).fetchone()[0]
            )
        assessment_rows.append(
            {
                "mint_address": mint,
                "roles": [role],
                "mint_snapshot_id": snapshot_id,
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


def seed_ready(storage):
    promote_phase8(storage)
    portfolio_lineage = seed_portfolio_candidate_lineage(storage)
    bandit_lineage = seed_retraining_dataset_evidence(
        storage,
        cycle_id="cycle",
        dataset_version="ML_ACTION_DATASET_V1:test",
        dataset_sha256="deadbeef",
    )
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
    evidence(storage, STATIC_HEDGE_EVIDENCE_TYPE, "pool-a")
    evidence(
        storage,
        CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        "__CONTEXTUAL_BANDIT__",
        extra={"dataset_lineage": bandit_lineage},
    )


def test_work_queue_surfaces_concrete_missing_research(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for index, pool in enumerate(("pool-a", "pool-b", "pool-c")):
        save_pool(
            storage,
            pool,
            f"2026-09-23T1{index}:00:00+00:00",
        )

    queue = build_phase9_work_queue(storage)

    task_types = {item.task_type for item in queue.items}
    assert "PHASE8_PROMOTION_REQUIRED" in task_types
    assert "ADAPTIVE_MULTI_POOL" in task_types
    assert "MINT_SNAPSHOT" in task_types
    assert "WALLET_FLOW" in task_types
    assert "STATIC_HEDGE" in task_types
    assert "PORTFOLIO_ALLOCATION" in task_types
    assert "CONTEXTUAL_BANDIT" in task_types
    adaptive = next(
        item for item in queue.items
        if item.task_type == "ADAPTIVE_MULTI_POOL"
    )
    assert adaptive.shell_command is not None
    assert "phase9-research-validate" in adaptive.shell_command
    assert queue.candidate_pools == ("pool-a", "pool-b", "pool-c")


def test_work_queue_requests_bundle_refresh_after_new_evidence(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(storage, report=bundle)

    seed_mint_risk_lineage(storage, "pool-c")

    queue = build_phase9_work_queue(storage)

    refresh = [
        item for item in queue.items
        if item.task_type == "REFRESH_RESEARCH_BUNDLE"
    ]
    assert len(refresh) == 1
    assert "phase9-research-bundle" in refresh[0].shell_command
    assert queue.promotion_ready is False


def test_work_queue_surfaces_promotion_when_bundle_is_current(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    bundle = evaluate_phase9_research_bundle(storage)
    persist_phase9_research_bundle(storage, report=bundle)

    queue = build_phase9_work_queue(storage)

    promotion = [
        item for item in queue.items
        if item.task_type == "PERSIST_PHASE9_PROMOTION"
    ]
    assert len(promotion) == 1
    assert "phase9-validate" in promotion[0].shell_command
    assert queue.research_bundle_ready is True
    assert queue.promotion_ready is True


def test_work_queue_advances_to_mint_risk_after_snapshots(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for index, pool in enumerate(("pool-a", "pool-b", "pool-c")):
        save_pool(
            storage,
            pool,
            f"2026-09-23T1{index}:00:00+00:00",
        )
        save_mint_snapshot(storage, f"{pool}-x")
        save_mint_snapshot(storage, f"{pool}-y")

    queue = build_phase9_work_queue(
        storage,
        rpc_url="https://rpc.example.invalid",
    )

    task_types = {item.task_type for item in queue.items}
    assert "MINT_SNAPSHOT" not in task_types
    assert "MINT_RISK" in task_types
    mint_task = next(
        item for item in queue.items
        if item.task_type == "MINT_RISK"
    )
    assert mint_task.shell_command is not None
    assert "mint-risk-research" in mint_task.shell_command


def test_work_queue_mint_snapshot_command_uses_requested_rpc(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(
        storage,
        "pool-a",
        "2026-09-23T10:00:00+00:00",
    )

    queue = build_phase9_work_queue(
        storage,
        criteria=Phase9ResearchBundleCriteria(
            min_mint_risk_pools=1,
            min_wallet_flow_pools=1,
            min_static_hedge_pools=1,
        ),
        rpc_url="https://rpc.example.invalid",
    )

    task = next(
        item for item in queue.items
        if item.task_type == "MINT_SNAPSHOT"
    )
    assert task.shell_command is not None
    assert "https://rpc.example.invalid" in task.shell_command
    assert "inspect-mint" in task.shell_command
    assert "mint-snapshot-ingest" in task.shell_command


def test_work_queue_prefers_cycle_bound_bandit_when_lineage_exists(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_retraining_dataset_evidence(storage)

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "CONTEXTUAL_BANDIT"
    )
    assert task.scope == "cycle-lineage"
    assert task.shell_command is not None
    assert "contextual-bandit-cycle-research" in task.shell_command
    assert "--cycle-id cycle-lineage" in task.shell_command


def test_work_queue_repairs_invalid_wallet_flow_lineage(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
    )
    forged = dict(latest["evidence"])
    forged["source_event_sha256"] = "0" * 64
    storage.save_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool-a",
        as_of="2026-09-23T12:05:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "WALLET_FLOW_REPAIR"
        and item.scope == "pool-a"
    )
    assert task.shell_command is not None
    assert "wallet-flow-research --pool pool-a" in task.shell_command


def test_work_queue_repairs_invalid_bandit_lineage(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    latest = storage.latest_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
    )
    forged = dict(latest["evidence"])
    forged["dataset_lineage"] = {
        **forged["dataset_lineage"],
        "dataset_evidence_id": 999999,
    }
    storage.save_advanced_edge_evidence(
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
        pool_address="__CONTEXTUAL_BANDIT__",
        as_of="2026-09-23T12:06:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence=forged,
    )

    queue = build_phase9_work_queue(storage)

    task = next(
        item for item in queue.items
        if item.task_type == "CONTEXTUAL_BANDIT_REPAIR"
    )
    assert task.scope == "cycle"
    assert task.shell_command is not None
    assert "contextual-bandit-cycle-research" in task.shell_command
