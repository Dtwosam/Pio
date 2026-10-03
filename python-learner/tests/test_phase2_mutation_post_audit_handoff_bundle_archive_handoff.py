from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy/tools/check_phase2_mutation_post_audit_handoff_bundle_archive_handoff.py"
)
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_portable_archive_current_handoff",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def good_archive(**overrides):
    values = {
        "archive_path": "/archive/phase2-handoff.tar",
        "archive_sha256": "a" * 64,
        "archive_size": 4096,
        "archive_verified": True,
        "source_bundle_sha256": "b" * 64,
        "source_bundle_verified": True,
        "deterministic_metadata_valid": True,
        "temporary_extraction_performed": True,
        "authorizes_next_action": False,
        "requires_fresh_separate_mutation_authorization": True,
        "production_file_modified": False,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
        "mutation_executed": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def good_lifecycle(**overrides):
    values = {
        "state": "TIMER_ACTIVATION_READY",
        "next_action": "ACTIVATE_EVIDENCE_TIMER",
        "next_tool": "activate_phase2_isolated_timer.py",
        "next_parameters": {
            "runtime_root": "/opt/pio-phase2-runtime",
            "receipt": "/opt/pio/data/phase2-isolated-smoke-receipt.json",
        },
        "next_mutation_flag": "--apply",
        "attention_required": False,
        "blockers": (),
        "provider_rate_limit_incident": False,
        "provider_rate_limit_paused": False,
        "read_only": True,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def install(monkeypatch, *, archive=None, lifecycle=None):
    monkeypatch.setattr(
        MODULE.ARCHIVE,
        "verify_phase2_portable_bundle_archive",
        lambda **kwargs: archive or good_archive(),
    )
    monkeypatch.setattr(
        MODULE.LIFECYCLE,
        "inspect_lifecycle_handoff",
        lambda **kwargs: lifecycle or good_lifecycle(),
    )


def test_archive_handoff_uses_only_current_lifecycle_for_next_action(
    monkeypatch,
):
    install(monkeypatch)

    report = MODULE.inspect_phase2_portable_archive_current_handoff(
        archive_path="/archive/phase2-handoff.tar",
        repository_root=ROOT,
    )

    assert report.archive_verified is True
    assert report.source_bundle_verified is True
    assert report.evidence_lineage_verified is True
    assert report.current_state == "TIMER_ACTIVATION_READY"
    assert report.current_next_action == "ACTIVATE_EVIDENCE_TIMER"
    assert report.current_next_tool == "activate_phase2_isolated_timer.py"
    assert report.current_next_mutation_flag == "--apply"
    assert report.archive_influenced_current_action is False
    assert report.next_action_source == "CURRENT_LIFECYCLE_HANDOFF"
    assert report.authorizes_next_action is False
    assert report.requires_fresh_separate_mutation_authorization is True
    assert report.attention_required is False
    assert report.read_only is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False
    assert report.mutation_executed is False


def test_archive_identity_cannot_change_current_next_action(monkeypatch):
    lifecycle = good_lifecycle(
        state="SMOKE_REQUIRED",
        next_action="RUN_ONE_SHOT_SMOKE",
        next_tool="run_phase2_isolated_smoke.py",
        next_mutation_flag="--apply",
        attention_required=True,
        blockers=("SMOKE_RECEIPT_STALE",),
    )

    outputs = []
    for digest in ("1" * 64, "2" * 64):
        install(
            monkeypatch,
            archive=good_archive(archive_sha256=digest),
            lifecycle=lifecycle,
        )
        report = MODULE.inspect_phase2_portable_archive_current_handoff(
            archive_path="/archive/phase2-handoff.tar",
            repository_root=ROOT,
        )
        outputs.append(
            (
                report.current_state,
                report.current_next_action,
                report.current_next_tool,
                report.current_next_mutation_flag,
            )
        )

    assert outputs == [
        ("SMOKE_REQUIRED", "RUN_ONE_SHOT_SMOKE", "run_phase2_isolated_smoke.py", "--apply"),
        ("SMOKE_REQUIRED", "RUN_ONE_SHOT_SMOKE", "run_phase2_isolated_smoke.py", "--apply"),
    ]


def test_archive_handoff_preserves_current_rate_limit_pause(monkeypatch):
    install(
        monkeypatch,
        lifecycle=good_lifecycle(
            state="RATE_LIMIT_PAUSED",
            next_action="KEEP_TIMER_PAUSED_UNTIL_PROVIDER_RECOVERS",
            next_tool="check_phase2_isolated_operator_status.py",
            next_mutation_flag=None,
            attention_required=True,
            blockers=("ACTIVE_PROVIDER_RATE_LIMIT_INCIDENT",),
            provider_rate_limit_incident=True,
            provider_rate_limit_paused=True,
        ),
    )

    report = MODULE.inspect_phase2_portable_archive_current_handoff(
        archive_path="/archive/phase2-handoff.tar",
        repository_root=ROOT,
    )

    assert report.provider_rate_limit_incident is True
    assert report.provider_rate_limit_paused is True
    assert report.current_state == "RATE_LIMIT_PAUSED"
    assert report.attention_required is True
    assert report.blockers == ("ACTIVE_PROVIDER_RATE_LIMIT_INCIDENT",)


@pytest.mark.parametrize(
    "field,value",
    [
        ("archive_verified", False),
        ("source_bundle_verified", False),
        ("deterministic_metadata_valid", False),
        ("temporary_extraction_performed", False),
        ("authorizes_next_action", True),
        ("requires_fresh_separate_mutation_authorization", False),
        ("production_file_modified", True),
        ("rpc_called", True),
        ("database_write_performed", True),
        ("service_control_performed", True),
        ("mutation_executed", True),
    ],
)
def test_archive_handoff_rejects_historical_boundary_crossing(
    monkeypatch,
    field,
    value,
):
    install(monkeypatch, archive=good_archive(**{field: value}))

    with pytest.raises(ValueError, match="historical non-authorizing boundary"):
        MODULE.inspect_phase2_portable_archive_current_handoff(
            archive_path="/archive/phase2-handoff.tar",
            repository_root=ROOT,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("read_only", False),
        ("rpc_called", True),
        ("database_write_performed", True),
        ("service_control_performed", True),
    ],
)
def test_archive_handoff_rejects_current_lifecycle_boundary_crossing(
    monkeypatch,
    field,
    value,
):
    install(monkeypatch, lifecycle=good_lifecycle(**{field: value}))

    with pytest.raises(ValueError, match="current Phase-2 lifecycle"):
        MODULE.inspect_phase2_portable_archive_current_handoff(
            archive_path="/archive/phase2-handoff.tar",
            repository_root=ROOT,
        )
