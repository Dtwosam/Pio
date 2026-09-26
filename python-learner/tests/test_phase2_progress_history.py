import pytest

from meteora_learner.phase2_evidence_cycle import (
    PHASE2_EVIDENCE_CYCLE_PROGRESS_TYPE,
)
from meteora_learner.phase2_progress_history import (
    build_phase2_progress_history,
)
from meteora_learner.storage import Storage


POOL = "pool"


def save_progress(
    storage,
    *,
    as_of,
    status,
    reconciliation,
    calibration,
    work_queue_items,
    task_counts,
    qualified=False,
    actionable=False,
    live_authorized=False,
    promotion_gate_evaluated=False,
    phase_promotion_performed=False,
):
    return storage.save_advanced_edge_evidence(
        edge_type=PHASE2_EVIDENCE_CYCLE_PROGRESS_TYPE,
        pool_address=POOL,
        as_of=as_of,
        status=status,
        qualified=qualified,
        evidence={
            "pool_address": POOL,
            "finished_at": as_of,
            "overall_status": status.removeprefix("COLLECTION_"),
            "stages_successful": 4,
            "stages_partial": 3,
            "stages_failed": 0,
            "stage_statuses": [],
            "reconciliation": reconciliation,
            "calibration": calibration,
            "work_queue_items": work_queue_items,
            "work_queue_task_counts": [
                {"task_type": name, "count": count}
                for name, count in task_counts
            ],
            "read_only": True,
            "actionable": actionable,
            "live_authorized": live_authorized,
            "promotion_gate_evaluated": promotion_gate_evaluated,
            "phase_promotion_performed": phase_promotion_performed,
            "qualified": qualified,
        },
    )


def test_phase2_progress_history_reports_signed_numeric_deltas(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_progress(
        storage,
        as_of="2026-09-26T18:00:00+00:00",
        status="COLLECTION_PARTIAL",
        reconciliation={
            "amount_positions_exact": 1,
            "fee_intervals_exact": 0,
            "strict_math_gate_passed": False,
        },
        calibration={
            "composition_exact_samples": 0,
            "add_execution_matched_events": 17,
            "evidence_gap_count": 6,
        },
        work_queue_items=12,
        task_counts=(("VERIFY_PRESTATE", 8), ("INSPECT_TRANSACTION", 4)),
    )
    save_progress(
        storage,
        as_of="2026-09-26T18:15:00+00:00",
        status="COLLECTION_PARTIAL",
        reconciliation={
            "amount_positions_exact": 3,
            "fee_intervals_exact": 2,
            "strict_math_gate_passed": False,
        },
        calibration={
            "composition_exact_samples": 2,
            "add_execution_matched_events": 19,
            "evidence_gap_count": 4,
        },
        work_queue_items=7,
        task_counts=(("VERIFY_PRESTATE", 5), ("INSPECT_TRANSACTION", 2)),
    )

    report = build_phase2_progress_history(
        storage,
        pool_address=POOL,
    )

    assert report.snapshots_returned == 2
    assert report.latest is not None
    assert report.previous is not None
    assert report.latest.as_of == "2026-09-26T18:15:00+00:00"
    assert report.previous.as_of == "2026-09-26T18:00:00+00:00"
    deltas = {
        item.metric: item.delta
        for item in report.numeric_deltas
    }
    assert deltas["reconciliation.amount_positions_exact"] == 2
    assert deltas["reconciliation.fee_intervals_exact"] == 2
    assert deltas["calibration.composition_exact_samples"] == 2
    assert deltas["calibration.add_execution_matched_events"] == 2
    assert deltas["calibration.evidence_gap_count"] == -2
    assert deltas["work_queue_items"] == -5
    assert deltas["work_queue.VERIFY_PRESTATE"] == -3
    assert deltas["work_queue.INSPECT_TRANSACTION"] == -2
    assert "reconciliation.strict_math_gate_passed" not in deltas
    assert report.read_only is True


def test_phase2_progress_history_is_pool_scoped_and_empty_is_valid(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_progress(
        storage,
        as_of="2026-09-26T18:00:00+00:00",
        status="COLLECTION_SUCCESS",
        reconciliation=None,
        calibration=None,
        work_queue_items=0,
        task_counts=(),
    )
    storage.save_advanced_edge_evidence(
        edge_type=PHASE2_EVIDENCE_CYCLE_PROGRESS_TYPE,
        pool_address="other-pool",
        as_of="2026-09-26T18:01:00+00:00",
        status="COLLECTION_SUCCESS",
        qualified=False,
        evidence={
            "promotion_gate_evaluated": False,
            "phase_promotion_performed": False,
            "actionable": False,
            "live_authorized": False,
            "stages_successful": 7,
            "stages_partial": 0,
            "stages_failed": 0,
            "work_queue_items": 0,
            "work_queue_task_counts": [],
        },
    )

    report = build_phase2_progress_history(
        storage,
        pool_address=POOL,
    )
    empty = build_phase2_progress_history(
        storage,
        pool_address="missing-pool",
    )

    assert report.snapshots_returned == 1
    assert report.latest is not None
    assert report.previous is None
    assert report.numeric_deltas == ()
    assert empty.snapshots_returned == 0
    assert empty.latest is None
    assert empty.previous is None


def test_phase2_progress_history_rejects_qualified_record(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_progress(
        storage,
        as_of="2026-09-26T18:00:00+00:00",
        status="COLLECTION_SUCCESS",
        reconciliation=None,
        calibration=None,
        work_queue_items=0,
        task_counts=(),
        qualified=True,
    )

    with pytest.raises(ValueError, match="qualified evidence"):
        build_phase2_progress_history(
            storage,
            pool_address=POOL,
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("actionable", True, "action boundary"),
        ("live_authorized", True, "live boundary"),
        ("promotion_gate_evaluated", True, "promotion boundary"),
        ("phase_promotion_performed", True, "promotion boundary"),
    ),
)
def test_phase2_progress_history_rejects_boundary_crossing(
    tmp_path,
    field,
    value,
    match,
):
    storage = Storage(tmp_path / "pio.db")
    kwargs = {field: value}
    save_progress(
        storage,
        as_of="2026-09-26T18:00:00+00:00",
        status="COLLECTION_SUCCESS",
        reconciliation=None,
        calibration=None,
        work_queue_items=0,
        task_counts=(),
        **kwargs,
    )

    with pytest.raises(ValueError, match=match):
        build_phase2_progress_history(
            storage,
            pool_address=POOL,
        )


def test_phase2_progress_history_requires_positive_limit(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="limit must be positive"):
        build_phase2_progress_history(
            storage,
            pool_address=POOL,
            limit=0,
        )
