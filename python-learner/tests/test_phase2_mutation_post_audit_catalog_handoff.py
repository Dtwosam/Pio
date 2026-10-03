from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_mutation_post_audit_catalog_handoff.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load(TOOL, "check_phase2_mutation_post_audit_catalog_handoff")


def snapshot_report(*, verified=True, authorizes=False):
    return SimpleNamespace(
        snapshot_path="/archive/catalog.json",
        snapshot_sha256="1" * 64,
        snapshot_verified=verified,
        artifacts_seen=3,
        artifacts_verified=3,
        catalog_source_commit="a" * 40,
        historical_snapshot_only=True,
        authorizes_next_action=authorizes,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def lifecycle_report(
    *,
    state="RUNNING_HEALTHY",
    next_action="MONITOR_ZERO_RPC_STATUS",
    next_tool="check_phase2_isolated_operator_status.py",
    next_flag=None,
    attention=False,
    blockers=(),
):
    return SimpleNamespace(
        state=state,
        next_action=next_action,
        next_tool=next_tool,
        next_parameters={"runtime_root": "/runtime"},
        next_mutation_flag=next_flag,
        attention_required=attention,
        blockers=tuple(blockers),
        provider_rate_limit_incident=False,
        provider_rate_limit_paused=False,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def install_reports(monkeypatch, snapshot, lifecycle):
    monkeypatch.setattr(
        MODULE.CATALOG_VERIFY,
        "verify_phase2_post_audit_catalog_snapshot",
        lambda **kwargs: snapshot,
    )
    monkeypatch.setattr(
        MODULE.LIFECYCLE,
        "inspect_lifecycle_handoff",
        lambda **kwargs: lifecycle,
    )


def test_handoff_keeps_historical_evidence_and_current_action_separate(
    monkeypatch,
):
    install_reports(
        monkeypatch,
        snapshot_report(),
        lifecycle_report(),
    )

    report = MODULE.inspect_phase2_post_audit_catalog_handoff(
        snapshot_path="/archive/catalog.json",
    )

    assert report.snapshot_verified is True
    assert report.evidence_lineage_verified is True
    assert report.historical_snapshot_only is True
    assert report.historical_authorizes_next_action is False
    assert report.current_state == "RUNNING_HEALTHY"
    assert report.current_next_action == "MONITOR_ZERO_RPC_STATUS"
    assert report.next_action_source == "CURRENT_LIFECYCLE_HANDOFF"
    assert report.snapshot_influenced_current_action is False
    assert report.authorizes_next_action is False
    assert report.requires_fresh_separate_mutation_authorization is True
    assert report.attention_required is False
    assert report.read_only is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False
    assert report.mutation_executed is False


def test_unverified_historical_snapshot_does_not_replace_current_action(
    monkeypatch,
):
    install_reports(
        monkeypatch,
        snapshot_report(verified=False),
        lifecycle_report(
            state="TIMER_ACTIVATION_READY",
            next_action="ACTIVATE_EVIDENCE_TIMER",
            next_tool="activate_phase2_isolated_timer.py",
            next_flag="--apply",
        ),
    )

    report = MODULE.inspect_phase2_post_audit_catalog_handoff(
        snapshot_path="/archive/catalog.json",
    )

    assert report.evidence_lineage_verified is False
    assert report.current_next_action == "ACTIVATE_EVIDENCE_TIMER"
    assert report.current_next_mutation_flag == "--apply"
    assert report.snapshot_influenced_current_action is False
    assert report.authorizes_next_action is False
    assert report.attention_required is True
    assert "HISTORICAL_CATALOG_SNAPSHOT_NOT_VERIFIED" in report.blockers


def test_current_lifecycle_attention_is_preserved(monkeypatch):
    install_reports(
        monkeypatch,
        snapshot_report(),
        lifecycle_report(
            state="RATE_LIMIT_PAUSED",
            next_action="KEEP_TIMER_PAUSED_UNTIL_PROVIDER_RECOVERS",
            attention=True,
            blockers=("ACTIVE_PROVIDER_RATE_LIMIT_INCIDENT",),
        ),
    )

    report = MODULE.inspect_phase2_post_audit_catalog_handoff(
        snapshot_path="/archive/catalog.json",
    )

    assert report.snapshot_verified is True
    assert report.current_attention_required is True
    assert report.attention_required is True
    assert report.current_blockers == ("ACTIVE_PROVIDER_RATE_LIMIT_INCIDENT",)
    assert report.blockers == ("ACTIVE_PROVIDER_RATE_LIMIT_INCIDENT",)


def test_handoff_rejects_historical_snapshot_that_authorizes_action(
    monkeypatch,
):
    install_reports(
        monkeypatch,
        snapshot_report(authorizes=True),
        lifecycle_report(),
    )

    with pytest.raises(ValueError, match="evidence-only boundary"):
        MODULE.inspect_phase2_post_audit_catalog_handoff(
            snapshot_path="/archive/catalog.json",
        )


def test_handoff_rejects_current_lifecycle_boundary_crossing(monkeypatch):
    lifecycle = lifecycle_report()
    lifecycle.rpc_called = True
    install_reports(
        monkeypatch,
        snapshot_report(),
        lifecycle,
    )

    with pytest.raises(ValueError, match="read-only boundary"):
        MODULE.inspect_phase2_post_audit_catalog_handoff(
            snapshot_path="/archive/catalog.json",
        )


def test_historical_snapshot_never_selects_current_next_step(monkeypatch):
    first = lifecycle_report(
        state="SMOKE_REQUIRED",
        next_action="RUN_ONE_SHOT_SMOKE",
        next_tool="run_phase2_isolated_smoke.py",
        next_flag="--apply",
    )
    install_reports(monkeypatch, snapshot_report(), first)
    report_a = MODULE.inspect_phase2_post_audit_catalog_handoff(
        snapshot_path="/archive/catalog.json",
    )

    second = lifecycle_report(
        state="RUNNING_HEALTHY",
        next_action="MONITOR_ZERO_RPC_STATUS",
        next_tool="check_phase2_isolated_operator_status.py",
    )
    install_reports(monkeypatch, snapshot_report(), second)
    report_b = MODULE.inspect_phase2_post_audit_catalog_handoff(
        snapshot_path="/archive/catalog.json",
    )

    assert report_a.snapshot_sha256 == report_b.snapshot_sha256
    assert report_a.current_next_action == "RUN_ONE_SHOT_SMOKE"
    assert report_b.current_next_action == "MONITOR_ZERO_RPC_STATUS"
    assert report_a.snapshot_influenced_current_action is False
    assert report_b.snapshot_influenced_current_action is False



def freshness_report(
    *,
    verified=True,
    snapshot_path="/archive/catalog.json",
    snapshot_sha256="1" * 64,
):
    return SimpleNamespace(
        snapshot_path=snapshot_path,
        snapshot_sha256=snapshot_sha256,
        artifact_directory="/fresh/archive",
        artifacts_reverified=True,
        fresh_reverification_verified=verified,
        authorizes_next_action=False,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def test_handoff_optionally_requires_fresh_archive_reverification(monkeypatch):
    install_reports(
        monkeypatch,
        snapshot_report(),
        lifecycle_report(),
    )
    calls = []

    def fresh(**kwargs):
        calls.append(kwargs)
        return freshness_report()

    monkeypatch.setattr(
        MODULE.CATALOG_FRESHNESS,
        "freshly_reverify_phase2_post_audit_catalog",
        fresh,
    )

    report = MODULE.inspect_phase2_post_audit_catalog_handoff(
        snapshot_path="/archive/catalog.json",
        artifact_directory="/fresh/archive",
        artifact_pattern="*.post-audit.json",
    )

    assert report.fresh_reverification_requested is True
    assert report.artifacts_reverified is True
    assert report.fresh_reverification_verified is True
    assert report.fresh_snapshot_identity_matches is True
    assert report.fresh_artifact_directory == "/fresh/archive"
    assert report.evidence_lineage_verified is True
    assert report.attention_required is False
    assert report.snapshot_influenced_current_action is False
    assert report.authorizes_next_action is False
    assert calls == [
        {
            "snapshot_path": "/archive/catalog.json",
            "artifact_directory": "/fresh/archive",
            "pattern": "*.post-audit.json",
            "repository_root": MODULE.CATALOG_VERIFY.REPO_ROOT,
        }
    ]


def test_fresh_archive_drift_blocks_evidence_lineage_not_current_action(
    monkeypatch,
):
    install_reports(
        monkeypatch,
        snapshot_report(),
        lifecycle_report(
            state="SMOKE_REQUIRED",
            next_action="RUN_ONE_SHOT_SMOKE",
            next_tool="run_phase2_isolated_smoke.py",
            next_flag="--apply",
        ),
    )
    monkeypatch.setattr(
        MODULE.CATALOG_FRESHNESS,
        "freshly_reverify_phase2_post_audit_catalog",
        lambda **kwargs: freshness_report(verified=False),
    )

    report = MODULE.inspect_phase2_post_audit_catalog_handoff(
        snapshot_path="/archive/catalog.json",
        artifact_directory="/fresh/archive",
    )

    assert report.snapshot_verified is True
    assert report.fresh_reverification_verified is False
    assert report.evidence_lineage_verified is False
    assert report.current_next_action == "RUN_ONE_SHOT_SMOKE"
    assert report.current_next_mutation_flag == "--apply"
    assert report.snapshot_influenced_current_action is False
    assert report.authorizes_next_action is False
    assert report.attention_required is True
    assert "FRESH_ARCHIVE_REVERIFICATION_FAILED" in report.blockers


def test_static_handoff_does_not_require_archive_reverification(monkeypatch):
    install_reports(
        monkeypatch,
        snapshot_report(),
        lifecycle_report(),
    )
    monkeypatch.setattr(
        MODULE.CATALOG_FRESHNESS,
        "freshly_reverify_phase2_post_audit_catalog",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("fresh verifier must not run")
        ),
    )

    report = MODULE.inspect_phase2_post_audit_catalog_handoff(
        snapshot_path="/archive/catalog.json",
    )

    assert report.fresh_reverification_requested is False
    assert report.artifacts_reverified is False
    assert report.fresh_reverification_verified is None
    assert report.fresh_snapshot_identity_matches is None
    assert report.fresh_artifact_directory is None
    assert report.evidence_lineage_verified is True


def test_handoff_rejects_fresh_reverification_boundary_crossing(monkeypatch):
    install_reports(
        monkeypatch,
        snapshot_report(),
        lifecycle_report(),
    )
    fresh = freshness_report()
    fresh.rpc_called = True
    monkeypatch.setattr(
        MODULE.CATALOG_FRESHNESS,
        "freshly_reverify_phase2_post_audit_catalog",
        lambda **kwargs: fresh,
    )

    with pytest.raises(ValueError, match="fresh post-audit catalog"):
        MODULE.inspect_phase2_post_audit_catalog_handoff(
            snapshot_path="/archive/catalog.json",
            artifact_directory="/fresh/archive",
        )



def test_fresh_snapshot_identity_change_blocks_combined_lineage(monkeypatch):
    install_reports(
        monkeypatch,
        snapshot_report(),
        lifecycle_report(),
    )
    monkeypatch.setattr(
        MODULE.CATALOG_FRESHNESS,
        "freshly_reverify_phase2_post_audit_catalog",
        lambda **kwargs: freshness_report(
            verified=True,
            snapshot_sha256="9" * 64,
        ),
    )

    report = MODULE.inspect_phase2_post_audit_catalog_handoff(
        snapshot_path="/archive/catalog.json",
        artifact_directory="/fresh/archive",
    )

    assert report.snapshot_verified is True
    assert report.fresh_reverification_verified is True
    assert report.fresh_snapshot_identity_matches is False
    assert report.evidence_lineage_verified is False
    assert report.snapshot_influenced_current_action is False
    assert report.authorizes_next_action is False
    assert report.attention_required is True
    assert (
        "FRESH_REVERIFICATION_SNAPSHOT_IDENTITY_MISMATCH"
        in report.blockers
    )
