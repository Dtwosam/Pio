import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from meteora_learner.phase2_evidence_cycle import (
    PHASE2_EVIDENCE_CYCLE_PROGRESS_TYPE,
    build_phase2_evidence_cycle_progress,
    persist_phase2_evidence_cycle_progress,
    run_phase2_read_only_evidence_cycle,
)
from meteora_learner.phase2_rpc_guard import Phase2RpcRateLimited
from meteora_learner.storage import Storage


class Result(SimpleNamespace):
    def to_record(self):
        return dict(self.__dict__)


def install_successes(monkeypatch, calls):
    def quotes(*args, **kwargs):
        calls.append("quotes")
        return Result(quotes_failed=0)

    def positions(*args, **kwargs):
        calls.append("positions")
        return Result(failures=0, discovery_truncated=False)

    def reinspect(*args, **kwargs):
        calls.append("reinspect")
        return Result(signatures_failed=0)

    def prestates(*args, **kwargs):
        calls.append("prestates")
        return Result(failures=0)

    def reconciliation(*args, **kwargs):
        calls.append("reconciliation")
        return Result(strict_math_gate_passed=True)

    def evidence(*args, **kwargs):
        calls.append("evidence")
        return Result(evidence_gaps=())

    def queue(*args, **kwargs):
        calls.append("queue")
        return Result(items=())

    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.collect_phase2_research_quotes",
        quotes,
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.collect_phase2_position_observations",
        positions,
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.run_phase2_calibration_reinspection",
        reinspect,
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.run_phase2_prestate_verifications",
        prestates,
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_reconciliation_corpus",
        reconciliation,
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_phase2_calibration_evidence",
        evidence,
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_calibration_work_queue",
        queue,
    )


def test_evidence_cycle_runs_read_only_stages_in_order(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "pio.db")
    calls = []
    install_successes(monkeypatch, calls)

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )

    assert calls == [
        "quotes",
        "positions",
        "reinspect",
        "prestates",
        "reconciliation",
        "evidence",
        "queue",
    ]
    assert report.stages_successful == 7
    assert report.stages_partial == 0
    assert report.stages_failed == 0
    assert [item.status for item in report.stages] == [
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
    ]
    assert report.collection_scope == "POOL"
    assert report.final_evidence_scope == "DATABASE_GLOBAL"
    assert report.read_only is True
    assert report.actionable is False
    assert report.live_authorized is False
    assert report.promotion_gate_evaluated is False
    assert report.phase_promotion_performed is False
    assert report.detector_cursor_untouched is True
    assert report.service_control_performed is False


def test_evidence_cycle_marks_collector_failures_partial(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.collect_phase2_research_quotes",
        lambda *args, **kwargs: Result(quotes_failed=1),
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.collect_phase2_position_observations",
        lambda *args, **kwargs: Result(
            failures=2,
            discovery_truncated=False,
        ),
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.run_phase2_calibration_reinspection",
        lambda *args, **kwargs: Result(signatures_failed=1),
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.run_phase2_prestate_verifications",
        lambda *args, **kwargs: Result(failures=1),
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_reconciliation_corpus",
        lambda *args, **kwargs: Result(strict_math_gate_passed=False),
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_phase2_calibration_evidence",
        lambda *args, **kwargs: Result(evidence_gaps=("gap",)),
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_calibration_work_queue",
        lambda *args, **kwargs: Result(items=("task",)),
    )

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )

    assert report.stages_partial == 7
    assert report.stages_failed == 0
    assert report.stages_successful == 0
    assert [item.status for item in report.stages] == [
        "PARTIAL",
        "PARTIAL",
        "PARTIAL",
        "PARTIAL",
        "PARTIAL",
        "PARTIAL",
        "PARTIAL",
    ]
    assert report.calibration_evidence["evidence_gaps"] == ("gap",)
    assert report.actionable is False


def test_evidence_cycle_isolates_stage_exception_and_hides_error_text(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []
    install_successes(monkeypatch, calls)
    secret = "https://user:secret@example.invalid/rpc"

    def broken_quotes(*args, **kwargs):
        calls.append("quotes-failed")
        raise RuntimeError(f"request failed at {secret}")

    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.collect_phase2_research_quotes",
        broken_quotes,
    )

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )

    assert report.stages_failed == 1
    failed = report.stages[0]
    assert failed.name == "RESEARCH_QUOTES"
    assert failed.status == "FAILED"
    assert failed.failure_category == "QUOTE_STAGE_FAILED"
    assert failed.result is None
    assert calls == [
        "quotes-failed",
        "positions",
        "reinspect",
        "prestates",
        "reconciliation",
        "evidence",
        "queue",
    ]
    encoded = str(report.to_record())
    assert secret not in encoded
    assert "request failed" not in encoded


def test_evidence_cycle_keeps_final_snapshots_optional_on_failure(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []
    install_successes(monkeypatch, calls)

    def broken_evidence(*args, **kwargs):
        raise ValueError("private diagnostic")

    def broken_queue(*args, **kwargs):
        raise ValueError("private queue diagnostic")

    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_phase2_calibration_evidence",
        broken_evidence,
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_calibration_work_queue",
        broken_queue,
    )

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )

    assert report.stages_failed == 2
    assert report.calibration_evidence is None
    assert report.work_queue is None
    assert report.stages[-2].failure_category == (
        "CALIBRATION_EVIDENCE_FAILED"
    )
    assert report.stages[-1].failure_category == "WORK_QUEUE_FAILED"
    assert "private diagnostic" not in str(report.to_record())


def test_evidence_cycle_requires_pool(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="pool_address is required"):
        run_phase2_read_only_evidence_cycle(
            storage,
            pool_address="",
            executor_path="/executor",
        )



def test_evidence_cycle_stays_partial_when_only_final_work_remains(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []
    install_successes(monkeypatch, calls)
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_phase2_calibration_evidence",
        lambda *args, **kwargs: Result(
            evidence_gaps=("no exact composition-fee reconciliation samples",)
        ),
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_calibration_work_queue",
        lambda *args, **kwargs: Result(items=("future-sample",)),
    )

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )

    assert [item.status for item in report.stages[:5]] == [
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
    ]
    assert report.stages[-2].status == "PARTIAL"
    assert report.stages[-1].status == "PARTIAL"
    assert report.stages_partial == 2
    assert report.stages_failed == 0
    assert report.actionable is False



def test_evidence_cycle_surfaces_reconciliation_failure_without_leaking_text(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []
    install_successes(monkeypatch, calls)
    secret = "https://user:secret@example.invalid/rpc"

    def broken_reconciliation(*args, **kwargs):
        raise RuntimeError(f"internal failure at {secret}")

    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_reconciliation_corpus",
        broken_reconciliation,
    )

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )

    stage = next(
        item for item in report.stages
        if item.name == "RECONCILIATION_CORPUS"
    )
    assert stage.status == "FAILED"
    assert stage.failure_category == "RECONCILIATION_CORPUS_FAILED"
    assert report.reconciliation_corpus is None
    encoded = str(report.to_record())
    assert secret not in encoded
    assert "internal failure" not in encoded



def test_evidence_cycle_progress_compacts_technical_counts(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []
    install_successes(monkeypatch, calls)
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_reconciliation_corpus",
        lambda *args, **kwargs: Result(
            positions_seen=4,
            amount_positions_eligible=3,
            amount_positions_exact=2,
            amount_positions_provenance_ineligible=1,
            amount_bins_checked=12,
            amount_mismatched_bins=1,
            fee_intervals_seen=5,
            fee_intervals_eligible=4,
            fee_intervals_exact=3,
            fee_intervals_provenance_ineligible=1,
            fee_bins_checked=9,
            fee_mismatched_bins=1,
            reward_intervals_seen=5,
            reward_intervals_eligible=2,
            reward_intervals_exact=1,
            reward_intervals_provenance_ineligible=1,
            reward_bins_checked=6,
            reward_bins_with_checkpoint_growth=2,
            reward_mismatched_bins=1,
            strict_math_gate_passed=False,
        ),
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_phase2_calibration_evidence",
        lambda *args, **kwargs: Result(
            add_positions=21,
            composition_add_events=17,
            composition_eligible_samples=2,
            composition_exact_samples=1,
            composition_mismatched_samples=1,
            composition_ineligible_samples=15,
            add_execution_events=19,
            add_execution_request_decodes=18,
            add_execution_matched_events=17,
            add_execution_unmatched_samples=2,
            add_active_guard_samples=12,
            rebalance_execution_events=12,
            rebalance_execution_request_decodes=12,
            rebalance_execution_matched_events=12,
            rebalance_active_guard_samples=12,
            transaction_fee_samples=42,
            evidence_gaps=("composition", "fee intervals"),
        ),
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.build_calibration_work_queue",
        lambda *args, **kwargs: Result(
            items=(
                {"task_type": "VERIFY_PRESTATE"},
                {"task_type": "VERIFY_PRESTATE"},
                {"task_type": "INSPECT_TRANSACTION"},
            )
        ),
    )

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )
    progress = build_phase2_evidence_cycle_progress(report)

    assert progress.overall_status == "PARTIAL"
    assert progress.reconciliation["positions_seen"] == 4
    assert progress.reconciliation["fee_intervals_exact"] == 3
    assert progress.calibration["add_positions"] == 21
    assert progress.calibration["composition_exact_samples"] == 1
    assert progress.calibration["evidence_gap_count"] == 2
    assert progress.work_queue_items == 3
    assert [
        (item.task_type, item.count)
        for item in progress.work_queue_task_counts
    ] == [
        ("VERIFY_PRESTATE", 2),
        ("INSPECT_TRANSACTION", 1),
    ]
    assert progress.rpc_rate_limited is False
    assert progress.rpc_circuit_open is False
    assert progress.stage_outcomes
    assert all(len(item) == 3 for item in progress.stage_outcomes)
    assert progress.qualified is False
    assert progress.promotion_gate_evaluated is False


def test_evidence_cycle_progress_persistence_is_non_qualified(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []
    install_successes(monkeypatch, calls)

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )
    evidence_id = persist_phase2_evidence_cycle_progress(
        storage,
        report=report,
    )

    assert evidence_id > 0
    saved = storage.latest_advanced_edge_evidence(
        edge_type=PHASE2_EVIDENCE_CYCLE_PROGRESS_TYPE,
        pool_address="pool",
    )
    assert saved is not None
    assert saved["qualified"] is False
    assert saved["status"] == "COLLECTION_SUCCESS"
    assert saved["as_of"] == "2026-09-26T19:00:00+00:00"
    assert saved["evidence"]["promotion_gate_evaluated"] is False
    assert saved["evidence"]["phase_promotion_performed"] is False


def test_evidence_cycle_progress_rejects_boundary_crossing(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []
    install_successes(monkeypatch, calls)

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )

    with pytest.raises(ValueError, match="read-only boundary"):
        build_phase2_evidence_cycle_progress(
            replace(report, promotion_gate_evaluated=True)
        )



def test_evidence_cycle_opens_rpc_circuit_after_position_rate_limit(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []
    install_successes(monkeypatch, calls)

    def rate_limited_positions(*args, **kwargs):
        calls.append("positions-rate-limited")
        raise Phase2RpcRateLimited("RPC_RATE_LIMITED")

    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.collect_phase2_position_observations",
        rate_limited_positions,
    )

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )

    assert calls == [
        "quotes",
        "positions-rate-limited",
        "reconciliation",
        "evidence",
        "queue",
    ]
    assert [item.status for item in report.stages] == [
        "SUCCESS",
        "FAILED",
        "SKIPPED",
        "SKIPPED",
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
    ]
    assert report.stages_failed == 1
    assert report.stages_skipped == 2
    assert report.stages[1].failure_category == "RPC_RATE_LIMITED"
    assert report.stages[2].failure_category == "RPC_CIRCUIT_OPEN"
    assert report.stages[3].failure_category == "RPC_CIRCUIT_OPEN"


def test_evidence_cycle_rate_limit_does_not_suppress_local_evidence(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []
    install_successes(monkeypatch, calls)

    def rate_limited_reinspection(*args, **kwargs):
        calls.append("reinspect-rate-limited")
        raise Phase2RpcRateLimited("RPC_RATE_LIMITED")

    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.run_phase2_calibration_reinspection",
        rate_limited_reinspection,
    )

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )

    assert calls == [
        "quotes",
        "positions",
        "reinspect-rate-limited",
        "reconciliation",
        "evidence",
        "queue",
    ]
    assert report.stages[2].status == "FAILED"
    assert report.stages[2].failure_category == "RPC_RATE_LIMITED"
    assert report.stages[3].status == "SKIPPED"
    assert report.stages[3].failure_category == "RPC_CIRCUIT_OPEN"
    assert report.stages[-3].name == "RECONCILIATION_CORPUS"
    assert report.stages[-2].name == "CALIBRATION_EVIDENCE"
    assert report.stages[-1].name == "CALIBRATION_WORK_QUEUE"


def test_evidence_cycle_progress_surfaces_skipped_rpc_stages(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []
    install_successes(monkeypatch, calls)

    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.collect_phase2_position_observations",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            Phase2RpcRateLimited("RPC_RATE_LIMITED")
        ),
    )

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )
    progress = build_phase2_evidence_cycle_progress(report)

    assert progress.overall_status == "FAILED"
    assert progress.stages_failed == 1
    assert progress.stages_skipped == 2
    assert progress.rpc_rate_limited is True
    assert progress.rpc_circuit_open is True
    assert ("TRANSACTION_REINSPECTION", "SKIPPED") in progress.stage_statuses
    assert ("PRESTATE_VERIFICATION", "SKIPPED") in progress.stage_statuses
    assert (
        "POSITION_OBSERVATIONS",
        "FAILED",
        "RPC_RATE_LIMITED",
    ) in progress.stage_outcomes
    assert (
        "TRANSACTION_REINSPECTION",
        "SKIPPED",
        "RPC_CIRCUIT_OPEN",
    ) in progress.stage_outcomes
    assert (
        "PRESTATE_VERIFICATION",
        "SKIPPED",
        "RPC_CIRCUIT_OPEN",
    ) in progress.stage_outcomes



def test_persisted_progress_keeps_rate_limit_category_without_raw_error_text(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []
    install_successes(monkeypatch, calls)
    secret = "https://rpc.invalid/?api-key=secret"

    def rate_limited_positions(*args, **kwargs):
        raise Phase2RpcRateLimited("RPC_RATE_LIMITED")

    monkeypatch.setattr(
        "meteora_learner.phase2_evidence_cycle.collect_phase2_position_observations",
        rate_limited_positions,
    )

    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address="pool",
        executor_path="/executor",
        now=lambda: "2026-09-26T19:00:00+00:00",
    )
    evidence_id = persist_phase2_evidence_cycle_progress(
        storage,
        report=report,
    )

    assert evidence_id > 0
    saved = storage.latest_advanced_edge_evidence(
        edge_type=PHASE2_EVIDENCE_CYCLE_PROGRESS_TYPE,
        pool_address="pool",
    )
    assert saved is not None
    evidence = saved["evidence"]
    assert evidence["rpc_rate_limited"] is True
    assert evidence["rpc_circuit_open"] is True
    assert [
        "POSITION_OBSERVATIONS",
        "FAILED",
        "RPC_RATE_LIMITED",
    ] in evidence["stage_outcomes"]
    encoded = json.dumps(evidence)
    assert secret not in encoded
