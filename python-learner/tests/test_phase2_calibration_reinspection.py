import json
import subprocess
from types import SimpleNamespace

import pytest

from meteora_learner.phase2_calibration_reinspection import (
    run_phase2_calibration_reinspection,
)
from meteora_learner.storage import Storage


def item(task_type, signature):
    return SimpleNamespace(
        task_type=task_type,
        signature=signature,
    )


def install_queue(monkeypatch, items):
    monkeypatch.setattr(
        "meteora_learner.phase2_calibration_reinspection.build_calibration_work_queue",
        lambda _: SimpleNamespace(items=tuple(items)),
    )


def test_reinspection_runs_only_read_only_decoder_tasks_and_dedupes(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    install_queue(
        monkeypatch,
        (
            item("INSPECT_TRANSACTION", "sig-a"),
            item("REINSPECT_TRANSACTION", "sig-a"),
            item("VERIFY_PRESTATE", "sig-verify"),
            item("REINSPECT_REBALANCE_TRANSACTION", "sig-b"),
            item("NEED_FUTURE_PRESTATE_SAMPLE", "sig-future"),
        ),
    )
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        payload = {
            "signature": command[2],
            "slot": 450800000,
            "block_time": 1_700_000_000,
            "succeeded": True,
            "events": [],
        }
        return subprocess.CompletedProcess(
            command, 0, json.dumps(payload), ""
        )

    report = run_phase2_calibration_reinspection(
        storage,
        executor_path="/executor",
        observed_at="2026-09-26T18:00:00+00:00",
        runner=runner,
    )

    assert report.queue_items_seen == 5
    assert report.reinspection_items_seen == 3
    assert report.unique_signatures_selected == 2
    assert report.selected_signatures == ("sig-a", "sig-b")
    assert report.signatures_succeeded == 2
    assert report.signatures_failed == 0
    assert report.read_only is True
    assert report.detector_cursor_untouched is True
    assert [command[1] for command in commands] == [
        "inspect-transaction-events-env",
        "inspect-transaction-events-env",
    ]
    assert [command[2] for command in commands] == ["sig-a", "sig-b"]
    assert all("RPC_URL" not in " ".join(command) for command in commands)


def test_reinspection_respects_explicit_task_budget(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "pio.db")
    install_queue(
        monkeypatch,
        (
            item("INSPECT_TRANSACTION", "sig-a"),
            item("INSPECT_TRANSACTION", "sig-b"),
            item("INSPECT_TRANSACTION", "sig-c"),
        ),
    )
    seen = []

    def runner(command, **kwargs):
        seen.append(command[2])
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "signature": command[2],
                    "slot": 1,
                    "events": [],
                }
            ),
            "",
        )

    report = run_phase2_calibration_reinspection(
        storage,
        executor_path="/executor",
        max_tasks=2,
        runner=runner,
    )

    assert report.unique_signatures_selected == 2
    assert report.selected_signatures == ("sig-a", "sig-b")
    assert seen == ["sig-a", "sig-b"]


def test_reinspection_failure_report_does_not_echo_executor_stderr(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    install_queue(
        monkeypatch,
        (item("INSPECT_TRANSACTION", "sig"),),
    )
    secret = "https://user:secret@example.invalid/rpc"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            f"transport failed for {secret}",
        )

    report = run_phase2_calibration_reinspection(
        storage,
        executor_path="/executor",
        runner=runner,
    )

    payload = json.dumps(report.to_record())
    assert report.signatures_failed == 1
    assert report.failure_details[0].category == "EXECUTOR_FAILED"
    assert secret not in payload
    assert "transport failed" not in payload


def test_reinspection_rejects_signature_mismatch_without_ingest(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    install_queue(
        monkeypatch,
        (item("INSPECT_TRANSACTION", "expected"),),
    )

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "signature": "different",
                    "slot": 1,
                    "events": [],
                }
            ),
            "",
        )

    report = run_phase2_calibration_reinspection(
        storage,
        executor_path="/executor",
        runner=runner,
    )

    assert report.signatures_succeeded == 0
    assert report.failure_details[0].category == "SIGNATURE_MISMATCH"
    with storage.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM chain_transaction_snapshots"
        ).fetchone()[0]
    assert count == 0


def test_reinspection_classifies_timeout(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "pio.db")
    install_queue(
        monkeypatch,
        (item("INSPECT_TRANSACTION", "sig"),),
    )

    def runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    report = run_phase2_calibration_reinspection(
        storage,
        executor_path="/executor",
        runner=runner,
    )

    assert report.failure_details[0].category == "EXECUTOR_TIMEOUT"


def test_reinspection_requires_positive_task_budget(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with pytest.raises(ValueError, match="max_tasks must be positive"):
        run_phase2_calibration_reinspection(
            storage,
            executor_path="/executor",
            max_tasks=0,
        )
