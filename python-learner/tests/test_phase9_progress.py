from meteora_learner.phase9_progress import evaluate_phase9_progress
from meteora_learner.phase9_validation import Phase9ResearchBundleCriteria
from meteora_learner.phase9_work_queue import (
    Phase9WorkItem,
    Phase9WorkQueue,
    persist_phase9_work_queue_snapshot,
)
from meteora_learner.storage import Storage


def queue(*, tasks, phase8=True, bundle=False, promotion=False):
    return Phase9WorkQueue(
        phase8_promoted=phase8,
        research_bundle_ready=bundle,
        promotion_ready=promotion,
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
