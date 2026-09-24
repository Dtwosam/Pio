import json
import sys

from meteora_learner import cli
from types import SimpleNamespace

import meteora_learner.phase8_operator_handoff as handoff_module
from meteora_learner.phase8_evidence_plan import (
    Phase8EvidenceDebtItem,
    Phase8EvidencePlan,
)
from meteora_learner.phase8_operator_handoff import (
    build_phase8_operator_handoff,
)
from meteora_learner.storage import Storage


def item(
    debt_type,
    *,
    operator_required=False,
    shell_command=None,
    scope="scope",
):
    return Phase8EvidenceDebtItem(
        priority=10,
        debt_type=debt_type,
        scope=scope,
        blocking=True,
        operator_required=operator_required,
        shell_command=shell_command,
        reason=f"{debt_type} reason",
    )


def plan(action=None, *, ready=False, current=False, items=None):
    return Phase8EvidencePlan(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        as_of="2026-09-24T12:00:00+00:00",
        promotion_ready=ready,
        persisted_phase8_current=current,
        next_action=action,
        items=tuple(
            items
            or (() if action is None else (action,))
        ),
        reasons=("plan reason",),
    )


def test_phase8_handoff_ready_when_promotion_current(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        handoff_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: plan(
            None,
            ready=True,
            current=True,
        ),
    )

    report = build_phase8_operator_handoff(storage)

    assert report.status == "READY"
    assert report.persisted_phase8_current is True
    assert report.operator_action_required is False
    assert report.manual_input_required is False


def test_phase8_handoff_routes_allowlisted_offline_work_to_runner(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = item(
        "RETRAIN_OFFLINE_TRAIN_READY",
        scope="cycle-1",
        shell_command=(
            "pio phase8-retrain-train-run --cycle-id cycle-1"
        ),
    )
    monkeypatch.setattr(
        handoff_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: plan(action),
    )

    report = build_phase8_operator_handoff(storage)

    assert report.status == "AUTOMATIC_ACTION"
    assert report.automatic_action_available is True
    assert report.operator_action_required is False
    assert report.suggested_command == (
        "pio phase8-evidence-run --max-steps 4"
    )


def test_phase8_handoff_waits_for_real_evidence(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = item(
        "WAITING_CHAIN_EVIDENCE",
        scope="champion-1",
    )
    monkeypatch.setattr(
        handoff_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: plan(action),
    )

    report = build_phase8_operator_handoff(storage)

    assert report.status == "WAITING_EVIDENCE"
    assert report.automatic_action_available is False
    assert report.operator_action_required is False
    assert report.suggested_command is None


def test_phase8_handoff_builds_non_inventing_retrain_input_handoff(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = item(
        "RETRAIN_DATASET_INPUTS_REQUIRED",
        scope="champion-1",
        operator_required=True,
        shell_command=(
            "pio phase8-retrain-input-template "
            "> phase8-retrain-inputs.json"
        ),
    )
    monkeypatch.setattr(
        handoff_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: plan(action),
    )
    monkeypatch.setattr(
        handoff_module,
        "build_phase8_retrain_input_template",
        lambda storage: {
            "research_only": True,
            "policy_actionable": False,
            "execution_wired": False,
            "champion_model_id": "champion-1",
            "champion_dataset_version": "dataset-v1",
            "pools": [
                {
                    "pool_address": "pool-a",
                    "amount_x": None,
                    "amount_y": None,
                    "network_cost_y_atomic": None,
                },
                {
                    "pool_address": None,
                    "amount_x": None,
                    "amount_y": None,
                    "network_cost_y_atomic": None,
                },
            ],
        },
    )

    report = build_phase8_operator_handoff(storage)

    assert report.status == "MANUAL_REQUIRED"
    assert report.operator_action_required is True
    assert report.manual_input_required is True
    assert report.automatic_action_available is False
    assert report.retrain_input_template is not None
    assert "pools[0].amount_x" in report.required_manual_fields
    assert "pools[1].pool_address" in report.required_manual_fields
    assert report.followup_commands[0] == (
        "pio phase8-retrain-inputs-check "
        "--file phase8-retrain-inputs.json --require-valid"
    )
    assert report.followup_commands[-1] == (
        "pio phase8-evidence-run --max-steps 4"
    )


def test_phase8_handoff_keeps_paper_transition_operator_owned(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = item(
        "PAPER_CHALLENGER_START_REQUIRED",
        scope="challenger-1",
        operator_required=True,
        shell_command=(
            "pio ml-start-paper --model-id challenger-1"
        ),
    )
    monkeypatch.setattr(
        handoff_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: plan(action),
    )

    report = build_phase8_operator_handoff(storage)

    assert report.status == "MANUAL_REQUIRED"
    assert report.operator_action_required is True
    assert report.manual_input_required is False
    assert report.automatic_action_available is False
    assert report.suggested_command == (
        "pio ml-start-paper --model-id challenger-1"
    )


def test_phase8_handoff_keeps_live_monitor_persistence_operator_owned(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = item(
        "LIVE_LABEL_DEPTH_REQUIRED",
        scope="champion-1",
        operator_required=False,
        shell_command=(
            "pio ml-live-monitor --model-id champion-1 --persist"
        ),
    )
    monkeypatch.setattr(
        handoff_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: plan(action),
    )

    report = build_phase8_operator_handoff(storage)

    assert report.status == "MANUAL_REQUIRED"
    assert report.automatic_action_available is False
    assert report.operator_action_required is True
    assert report.suggested_command == (
        "pio ml-live-monitor --model-id champion-1 --persist"
    )


def test_phase8_operator_handoff_cli_prints_manual_boundary(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    report = handoff_module.Phase8OperatorHandoff(
        research_only=True,
        read_only=True,
        policy_actionable=False,
        execution_wired=False,
        as_of="2026-09-24T12:00:00+00:00",
        status="MANUAL_REQUIRED",
        promotion_ready=False,
        persisted_phase8_current=False,
        debt_type="PAPER_CHALLENGER_START_REQUIRED",
        scope="challenger-1",
        reason="paper start requires operator action",
        automatic_action_available=False,
        operator_action_required=True,
        manual_input_required=False,
        suggested_command="pio ml-start-paper --model-id challenger-1",
        retrain_input_template=None,
        required_manual_fields=(),
        followup_commands=("pio phase8-evidence-plan",),
        operator_blockers=(),
        reasons=("plan reason",),
    )
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))
    monkeypatch.setattr(
        cli,
        "build_phase8_operator_handoff",
        lambda *args, **kwargs: report,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["pio", "phase8-operator-handoff"],
    )

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "MANUAL_REQUIRED"
    assert payload["debt_type"] == "PAPER_CHALLENGER_START_REQUIRED"
    assert payload["suggested_command"] == (
        "pio ml-start-paper --model-id challenger-1"
    )
