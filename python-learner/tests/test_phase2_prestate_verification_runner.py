import json
import subprocess
from types import SimpleNamespace

from meteora_learner.phase2_prestate_verification_runner import (
    run_phase2_prestate_verifications,
)
from meteora_learner.storage import Storage


def work_item(
    signature="sig",
    *,
    position="position",
    ix=2,
    task_type="VERIFY_PRESTATE",
):
    return SimpleNamespace(
        task_type=task_type,
        signature=signature,
        position_address=position,
        instruction_index=ix,
    )


def candidate(
    signature="sig",
    *,
    ix=2,
    snapshot="2026-09-26T18:00:00+00:00",
    capture_slot=100,
):
    return SimpleNamespace(
        signature=signature,
        parent_ix_index=ix,
        eligible_for_verification=True,
        capture_slot_start=capture_slot,
        capture_slot_end=capture_slot,
        transaction_slot=120,
        snapshot_observed_at=snapshot,
        verification_addresses=("pool", "array"),
        pool_address="pool",
    )


def install_inputs(monkeypatch, *, items, candidates):
    monkeypatch.setattr(
        "meteora_learner.phase2_prestate_verification_runner.build_calibration_work_queue",
        lambda _: SimpleNamespace(items=tuple(items)),
    )
    monkeypatch.setattr(
        "meteora_learner.phase2_prestate_verification_runner.build_composition_prestate_candidates",
        lambda *args, **kwargs: SimpleNamespace(
            candidates=tuple(candidates)
        ),
    )


def verifier_payload(signature, *, eligible):
    return {
        "signature": signature,
        "transaction_slot": 120,
        "capture_slot_start": 100,
        "capture_slot_end": 100,
        "eligible": eligible,
        "reasons": [] if eligible else ["intervening transaction"],
        "account_checks": [
            {
                "address": "pool",
                "target_signature_seen": True,
                "conflicting_transactions": [],
                "eligible": eligible,
            },
            {
                "address": "array",
                "target_signature_seen": True,
                "conflicting_transactions": [],
                "eligible": eligible,
            },
        ],
    }


def test_runner_ingests_positive_verifier_verdict(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "pio.db")
    install_inputs(
        monkeypatch,
        items=(work_item(),),
        candidates=(candidate(),),
    )
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(verifier_payload("sig", eligible=True)),
            "",
        )

    report = run_phase2_prestate_verifications(
        storage,
        executor_path="/executor",
        runner=runner,
    )

    assert report.verdicts_ingested == 1
    assert report.eligible_verdicts == 1
    assert report.ineligible_verdicts == 0
    assert report.failures == 0
    assert commands == [
        ["/executor", "verify-prestate-env", "sig", "100", "100", "pool", "array"]
    ]
    assert all("RPC_URL" not in value for value in commands[0])

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT signature, snapshot_observed_at, eligible
            FROM composition_prestate_verifications
            """
        ).fetchone()
    assert row == ("sig", "2026-09-26T18:00:00+00:00", 1)


def test_runner_persists_negative_verdict_as_valid_evidence(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    install_inputs(
        monkeypatch,
        items=(work_item(),),
        candidates=(candidate(),),
    )

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(verifier_payload("sig", eligible=False)),
            "",
        )

    report = run_phase2_prestate_verifications(
        storage,
        executor_path="/executor",
        runner=runner,
    )

    assert report.verdicts_ingested == 1
    assert report.eligible_verdicts == 0
    assert report.ineligible_verdicts == 1
    assert report.failures == 0
    with storage.connect() as conn:
        eligible = conn.execute(
            "SELECT eligible FROM composition_prestate_verifications"
        ).fetchone()[0]
    assert eligible == 0


def test_runner_refuses_non_single_context_candidate(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "pio.db")
    item = work_item()
    mixed = candidate()
    mixed.capture_slot_end = 101
    install_inputs(
        monkeypatch,
        items=(item,),
        candidates=(mixed,),
    )
    called = False

    def runner(command, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("executor must not run")

    report = run_phase2_prestate_verifications(
        storage,
        executor_path="/executor",
        runner=runner,
    )

    assert called is False
    assert report.candidates_selected == 0
    assert report.failures == 1
    assert report.failure_details[0].category == (
        "CANDIDATE_NOT_SINGLE_CONTEXT"
    )


def test_runner_rejects_verifier_slot_mismatch_without_ingest(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    install_inputs(
        monkeypatch,
        items=(work_item(),),
        candidates=(candidate(),),
    )
    payload = verifier_payload("sig", eligible=True)
    payload["transaction_slot"] = 121

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, json.dumps(payload), ""
        )

    report = run_phase2_prestate_verifications(
        storage,
        executor_path="/executor",
        runner=runner,
    )

    assert report.verdicts_ingested == 0
    assert report.failure_details[0].category == (
        "TRANSACTION_SLOT_MISMATCH"
    )
    with storage.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM composition_prestate_verifications"
        ).fetchone()[0]
    assert count == 0


def test_runner_does_not_echo_executor_stderr(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "pio.db")
    install_inputs(
        monkeypatch,
        items=(work_item(),),
        candidates=(candidate(),),
    )
    secret = "https://user:secret@example.invalid/rpc"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            f"failure at {secret}",
        )

    report = run_phase2_prestate_verifications(
        storage,
        executor_path="/executor",
        runner=runner,
    )

    encoded = json.dumps(report.to_record())
    assert report.failure_details[0].category == "EXECUTOR_FAILED"
    assert secret not in encoded
    assert "failure at" not in encoded


def test_runner_ignores_non_verification_work_items(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "pio.db")
    install_inputs(
        monkeypatch,
        items=(
            work_item(task_type="NEED_FUTURE_PRESTATE_SAMPLE"),
            work_item(
                signature="sig-2",
                task_type="REVIEW_COMPOSITION_MISMATCH",
            ),
        ),
        candidates=(),
    )

    report = run_phase2_prestate_verifications(
        storage,
        executor_path="/executor",
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("executor must not run")
        ),
    )

    assert report.verification_items_seen == 0
    assert report.candidates_selected == 0
    assert report.verdicts_ingested == 0
    assert report.failures == 0
