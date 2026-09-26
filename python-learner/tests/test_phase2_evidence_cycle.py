from types import SimpleNamespace

import pytest

from meteora_learner.phase2_evidence_cycle import (
    run_phase2_read_only_evidence_cycle,
)
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
        "evidence",
        "queue",
    ]
    assert report.stages_successful == 6
    assert report.stages_partial == 0
    assert report.stages_failed == 0
    assert [item.status for item in report.stages] == [
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
    ]
    assert report.read_only is True
    assert report.actionable is False
    assert report.live_authorized is False
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

    assert report.stages_partial == 4
    assert report.stages_failed == 0
    assert report.stages_successful == 2
    assert [item.status for item in report.stages[:4]] == [
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
