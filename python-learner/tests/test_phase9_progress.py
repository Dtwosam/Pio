from meteora_learner.phase9_progress import evaluate_phase9_progress
from meteora_learner.phase9_pool_activity_scan_state import (
    record_phase9_pool_activity_page,
)
from meteora_learner.phase9_validation import Phase9ResearchBundleCriteria
from meteora_learner.phase9_work_queue import (
    Phase9WorkItem,
    Phase9WorkQueue,
    persist_phase9_work_queue_snapshot,
)
from meteora_learner.storage import Storage


def queue(
    *,
    tasks,
    phase8=True,
    bundle=False,
    promotion=False,
    phase9_current=False,
    authorization=False,
    controlled=False,
    rollout=False,
    rollback=False,
    prewire=False,
    manifest=False,
    sources_current=False,
):
    return Phase9WorkQueue(
        phase8_promoted=phase8,
        research_bundle_ready=bundle,
        promotion_ready=promotion,
        phase9_current=phase9_current,
        policy_authorization_current=authorization,
        controlled_validation_current=controlled,
        rollout_simulation_current=rollout,
        rollback_simulation_current=rollback,
        prewire_ready=prewire,
        prewire_manifest_current=manifest,
        research_sources_current=sources_current,
        candidate_pools=("pool-a",),
        items=tuple(tasks),
    )


def task(task_type, scope):
    return Phase9WorkItem(
        task_type=task_type,
        scope=scope,
        reason=f"need {task_type}",
        shell_command="secret command that must not be persisted",
    )


def test_phase9_progress_tracks_resolved_and_new_blockers(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    criteria = Phase9ResearchBundleCriteria(
        min_mint_risk_pools=1,
        min_wallet_flow_pools=1,
        min_static_hedge_pools=1,
    )

    persist_phase9_work_queue_snapshot(
        storage,
        queue=queue(
            tasks=(
                task("MINT_RISK", "pool-a"),
                task("WALLET_FLOW", "pool-a"),
            ),
        ),
        criteria=criteria,
        created_at="2026-09-23T18:00:00+00:00",
    )
    persist_phase9_work_queue_snapshot(
        storage,
        queue=queue(
            tasks=(
                task("WALLET_FLOW", "pool-a"),
                task("STATIC_HEDGE", "pool-a"),
            ),
        ),
        criteria=criteria,
        created_at="2026-09-23T19:00:00+00:00",
    )

    report = evaluate_phase9_progress(storage)

    assert report.status == "EVIDENCE_PENDING"
    assert report.snapshot_count == 2
    assert report.integrity_verified is True
    assert report.first_task_count == 2
    assert report.latest_task_count == 2
    assert report.resolved_tasks == ("MINT_RISK:pool-a",)
    assert report.new_tasks == ("STATIC_HEDGE:pool-a",)
    assert report.phase8_current is True


def test_phase9_progress_reports_no_snapshots(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = evaluate_phase9_progress(storage)

    assert report.status == "NO_SNAPSHOTS"
    assert report.snapshot_count == 0
    assert report.integrity_verified is True
    assert report.promotion_ready is False


def test_phase9_progress_fails_closed_on_bad_snapshot_checksum(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO phase9_work_queue_snapshots(
                created_at, queue_sha256, criteria_json, state_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                "2026-09-23T18:00:00+00:00",
                "0" * 64,
                '{"min_mint_risk_pools":1}',
                (
                    '{"phase8_promoted":true,'
                    '"research_bundle_ready":false,'
                    '"promotion_ready":false,"items":[]}'
                ),
            ),
        )

    report = evaluate_phase9_progress(storage)

    assert report.status == "INTEGRITY_FAILED"
    assert report.integrity_verified is False
    assert any(
        "checksum does not match" in reason
        for reason in report.reasons
    )


def test_phase9_progress_advances_through_policy_evidence_states(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    criteria = Phase9ResearchBundleCriteria(
        min_mint_risk_pools=1,
        min_wallet_flow_pools=1,
        min_static_hedge_pools=1,
    )

    persist_phase9_work_queue_snapshot(
        storage,
        queue=queue(
            tasks=(task("ROLLBACK_SIMULATION", "metrics"),),
            bundle=True,
            promotion=True,
            phase9_current=True,
            authorization=True,
            controlled=True,
            rollout=True,
        ),
        criteria=criteria,
        created_at="2026-09-23T20:00:00+00:00",
    )
    report = evaluate_phase9_progress(storage)

    assert report.status == "ROLLOUT_SIMULATION_CURRENT"
    assert report.phase9_current is True
    assert report.policy_authorization_current is True
    assert report.controlled_validation_current is True
    assert report.rollout_simulation_current is True
    assert report.rollback_simulation_current is False
    assert report.prewire_ready is False

    persist_phase9_work_queue_snapshot(
        storage,
        queue=queue(
            tasks=(),
            bundle=True,
            promotion=True,
            phase9_current=True,
            authorization=True,
            controlled=True,
            rollout=True,
            rollback=True,
            prewire=True,
        ),
        criteria=criteria,
        created_at="2026-09-23T21:00:00+00:00",
    )
    report = evaluate_phase9_progress(storage)

    assert report.status == "PREWIRE_READY"
    assert report.rollback_simulation_current is True
    assert report.prewire_ready is True


def test_phase9_progress_reports_current_prewire_manifest(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    criteria = Phase9ResearchBundleCriteria(
        min_mint_risk_pools=1,
        min_wallet_flow_pools=1,
        min_static_hedge_pools=1,
    )
    persist_phase9_work_queue_snapshot(
        storage,
        queue=queue(
            tasks=(),
            bundle=True,
            promotion=True,
            phase9_current=True,
            authorization=True,
            controlled=True,
            rollout=True,
            rollback=True,
            prewire=True,
            manifest=True,
        ),
        criteria=criteria,
        created_at="2026-09-23T22:00:00+00:00",
    )

    report = evaluate_phase9_progress(storage)

    assert report.status == "PREWIRE_MANIFEST_CURRENT"
    assert report.prewire_ready is True
    assert report.prewire_manifest_current is True


def test_phase9_progress_exposes_wallet_activity_scan_without_snapshots(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    record_phase9_pool_activity_page(
        storage,
        pool_address="pool-b",
        next_before_signature="sig-b",
        has_more=True,
        signatures_scanned=25,
        matching_transactions=4,
        positions_discovered=3,
    )
    record_phase9_pool_activity_page(
        storage,
        pool_address="pool-a",
        next_before_signature=None,
        has_more=False,
        signatures_scanned=12,
        matching_transactions=2,
        positions_discovered=1,
    )

    report = evaluate_phase9_progress(storage)

    assert report.status == "NO_SNAPSHOTS"
    assert [item.pool_address for item in report.wallet_activity_scans] == [
        "pool-a",
        "pool-b",
    ]
    first, second = report.wallet_activity_scans
    assert first.backfill_exhausted is True
    assert first.pages_scanned == 1
    assert first.signatures_scanned == 12
    assert second.backfill_exhausted is False
    assert second.backfill_before_signature == "sig-b"
    assert second.positions_discovered == 3


def test_phase9_progress_reports_source_refresh_pending(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    criteria = Phase9ResearchBundleCriteria(
        min_mint_risk_pools=1,
        min_wallet_flow_pools=1,
        min_static_hedge_pools=1,
    )
    persist_phase9_work_queue_snapshot(
        storage,
        queue=queue(
            tasks=(
                task(
                    "RESEARCH_SOURCE_REFRESH",
                    "adaptive_regime,wallet_flow",
                ),
            ),
            bundle=True,
            promotion=True,
            sources_current=False,
        ),
        criteria=criteria,
        created_at="2026-09-23T22:10:00+00:00",
    )

    report = evaluate_phase9_progress(storage)

    assert report.status == "SOURCE_REFRESH_PENDING"
    assert report.research_sources_current is False
    assert report.latest_task_count == 1
    assert report.new_tasks == ()


def test_phase9_progress_exposes_current_research_sources(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    criteria = Phase9ResearchBundleCriteria(
        min_mint_risk_pools=1,
        min_wallet_flow_pools=1,
        min_static_hedge_pools=1,
    )
    persist_phase9_work_queue_snapshot(
        storage,
        queue=queue(
            tasks=(),
            bundle=True,
            promotion=True,
            sources_current=True,
        ),
        criteria=criteria,
        created_at="2026-09-23T22:15:00+00:00",
    )

    report = evaluate_phase9_progress(storage)

    assert report.research_sources_current is True
