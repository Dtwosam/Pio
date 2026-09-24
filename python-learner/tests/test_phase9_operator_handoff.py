import json
import sys

from types import SimpleNamespace

from meteora_learner import cli
import meteora_learner.phase9_operator_handoff as handoff_module
from meteora_learner.phase9_evidence_plan import (
    Phase9EvidenceDebtItem,
    Phase9EvidencePlan,
)
from meteora_learner.phase9_operator_handoff import (
    build_phase9_operator_handoff,
)
from meteora_learner.storage import Storage


def action(
    debt_type,
    *,
    actionable,
    scope="scope",
    command="pio safe-command",
):
    return Phase9EvidenceDebtItem(
        priority=10,
        debt_type=debt_type,
        scope=scope,
        blocking=True,
        actionable=actionable,
        current=0,
        required=1,
        remaining=1,
        shell_command=command,
        reason=f"{debt_type} reason",
    )


def plan(next_action, *, ready=False, items=None):
    return Phase9EvidencePlan(
        research_only=True,
        read_only_commands=True,
        policy_actionable=False,
        execution_wired=False,
        as_of="2026-09-24T12:00:00+00:00",
        research_bundle_ready=ready,
        source_ready=ready,
        history_capture_cycles_remaining=0,
        history_interval_seconds=3600,
        next_action=next_action,
        items=tuple(items or (() if next_action is None else (next_action,))),
        reasons=("plan reason",),
    )


def test_operator_handoff_reports_ready_without_actions(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        handoff_module,
        "build_phase9_evidence_plan",
        lambda *args, **kwargs: plan(None, ready=True),
    )

    report = build_phase9_operator_handoff(
        storage,
        as_of="2026-09-24T12:00:00+00:00",
    )

    assert report.status == "READY"
    assert report.research_bundle_ready is True
    assert report.suggested_command is None
    assert report.operator_action_required is False
    assert report.manual_input_required is False


def test_operator_handoff_preserves_automatic_action(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    next_action = action("CHAIN_HISTORY_DEPTH", actionable=True)
    monkeypatch.setattr(
        handoff_module,
        "build_phase9_evidence_plan",
        lambda *args, **kwargs: plan(next_action),
    )

    report = build_phase9_operator_handoff(storage)

    assert report.status == "AUTOMATIC_ACTION"
    assert report.automatic_action_available is True
    assert report.operator_action_required is False
    assert report.manual_input_required is False
    assert report.suggested_command == "pio safe-command"
    assert report.explicit_input_template is None


def test_operator_handoff_builds_non_inventing_explicit_input_handoff(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    next_action = action(
        "EXPLICIT_RESEARCH_INPUTS",
        actionable=True,
        scope="USER_ASSUMPTIONS_REQUIRED",
    )
    monkeypatch.setattr(
        handoff_module,
        "build_phase9_evidence_plan",
        lambda *args, **kwargs: plan(next_action),
    )
    monkeypatch.setattr(
        handoff_module,
        "evaluate_phase9_evidence_status",
        lambda *args, **kwargs: SimpleNamespace(
            sampling_pools=("pool-a", "pool-b", "pool-c")
        ),
    )

    report = build_phase9_operator_handoff(storage)

    assert report.status == "MANUAL_REQUIRED"
    assert report.operator_action_required is True
    assert report.manual_input_required is True
    assert report.automatic_action_available is False
    assert report.suggested_command == (
        "pio phase9-research-input-template "
        "--pools pool-a,pool-b,pool-c "
        "> phase9-research-inputs.json"
    )
    assert report.explicit_input_template is not None
    assert report.explicit_input_template["static_hedges"][0][
        "amount_x"
    ] is None
    assert "static_hedges[0].amount_x" in report.required_manual_fields
    assert "static_hedges[0].as_of" not in report.required_manual_fields
    assert "portfolio.budget_quote" in report.required_manual_fields
    assert report.followup_commands[0] == (
        "pio phase9-research-inputs-check "
        "--file phase9-research-inputs.json --require-valid"
    )
    assert report.followup_commands[-1] == (
        "pio phase9-evidence-run --max-steps 8"
    )


def test_operator_handoff_surfaces_non_actionable_upstream_blocker(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    blocker = action(
        "PHASE8_DEPENDENCY",
        actionable=False,
        scope="PHASE8_PROMOTION",
        command="pio phase8-evidence-plan",
    )
    monkeypatch.setattr(
        handoff_module,
        "build_phase9_evidence_plan",
        lambda *args, **kwargs: plan(blocker, items=(blocker,)),
    )

    report = build_phase9_operator_handoff(storage)

    assert report.status == "MANUAL_REQUIRED"
    assert report.operator_action_required is True
    assert report.manual_input_required is False
    assert report.automatic_action_available is False
    assert report.suggested_command == "pio phase8-operator-handoff"
    assert report.followup_commands == (
        "pio phase8-evidence-run --max-steps 4",
        "pio phase8-evidence-plan",
        "pio phase9-evidence-run --max-steps 8",
    )
    assert report.blockers[0]["debt_type"] == "PHASE8_DEPENDENCY"


def test_operator_handoff_cli_prints_manual_handoff(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    next_action = action(
        "PHASE8_DEPENDENCY",
        actionable=False,
        scope="PHASE8_PROMOTION",
        command="pio phase8-evidence-plan",
    )
    report = handoff_module.Phase9OperatorHandoff(
        research_only=True,
        read_only=True,
        policy_actionable=False,
        execution_wired=False,
        as_of="2026-09-24T12:00:00+00:00",
        status="MANUAL_REQUIRED",
        research_bundle_ready=False,
        debt_type=next_action.debt_type,
        scope=next_action.scope,
        reason=next_action.reason,
        automatic_action_available=False,
        operator_action_required=True,
        manual_input_required=False,
        suggested_command="pio phase8-operator-handoff",
        explicit_input_template=None,
        required_manual_fields=(),
        followup_commands=(),
        blockers=(),
        reasons=("plan reason",),
    )
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))
    monkeypatch.setattr(
        cli,
        "build_phase9_operator_handoff",
        lambda *args, **kwargs: report,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["pio", "phase9-operator-handoff"],
    )

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "MANUAL_REQUIRED"
    assert payload["debt_type"] == "PHASE8_DEPENDENCY"
    assert payload["suggested_command"] == "pio phase8-operator-handoff"
