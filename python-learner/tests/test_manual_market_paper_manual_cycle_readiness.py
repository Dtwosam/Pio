from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_manual_market_paper_manual_cycle_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_manual_cycle_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _report() -> dict:
    identity = {
        "format_version": MODULE.FORMAT_VERSION,
        "artifact_type": MODULE.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                MODULE.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "saved_post_mutation_audit_sha256": "1" * 64,
        "fresh_post_mutation_audit_sha256": "1" * 64,
        "production_repository": "/opt/pio",
        "mutation_receipt_sha256": "2" * 64,
        "execution_precheck_sha256": "3" * 64,
        "approver_principal": "wyck@example.com",
        "approval_id": "01234567-89ab-4def-8123-456789abcdef",
        "runtime_manifest_git_blob": MODULE.REVIEWED_SOURCE_BLOBS[
            MODULE.RUNTIME_MANIFEST
        ],
        "manual_cycle_cli_git_blob": MODULE.REVIEWED_SOURCE_BLOBS[
            MODULE.MANUAL_CYCLE_CLI
        ],
        "fresh_post_mutation_audit_matches_saved": True,
        "post_mutation_audit_ready": True,
        "runtime_files_deployed": True,
        "manual_paper_runtime_ready": True,
        "manual_mode_safe": True,
        "operational_services_healthy": True,
        "activation_state_unchanged": True,
        "activation_observed": False,
        "runtime_manifest_manual_only": True,
        "manual_cycle_cli_reviewed": True,
        "manual_cycle_readiness_ready": True,
        "requires_separate_manual_cycle_authorization": True,
        "manual_cycle_execution_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
    }
    return {
        **identity,
        "manual_cycle_readiness_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["manual_cycle_readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def _saved_audit(production: Path) -> dict:
    return {
        "post_mutation_audit_ready": True,
        "post_mutation_audit_sha256": "1" * 64,
        "production_repository": str(production),
        "mutation_receipt_sha256": "2" * 64,
        "execution_precheck_sha256": "3" * 64,
        "approver_principal": "wyck@example.com",
        "approval_id": "01234567-89ab-4def-8123-456789abcdef",
        "runtime_files_deployed": True,
        "manual_paper_runtime_ready": True,
        "manual_mode_safe": True,
        "operational_services_healthy": True,
        "activation_state_unchanged": True,
        "activation_observed": False,
    }


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_runtime_manifest_remains_manual_only_and_contains_reviewed_cli():
    manifest = MODULE._validate_runtime_manifest(ROOT)

    assert manifest["activation_mode"] == "MANUAL_ONLY"
    assert manifest["paper_only"] is True
    assert manifest["deployment_guard_apply_locked"] is True
    assert (
        manifest["deployment_target_file_blobs"][str(MODULE.MANUAL_CYCLE_CLI)]
        == MODULE.REVIEWED_SOURCE_BLOBS[MODULE.MANUAL_CYCLE_CLI]
    )


def test_ready_report_still_authorizes_no_execution_or_activation():
    report = _report()

    MODULE.validate_manual_cycle_readiness(copy.deepcopy(report))

    assert report["manual_cycle_readiness_ready"] is True
    assert report["requires_separate_manual_cycle_authorization"] is True
    assert report["manual_cycle_execution_authorized"] is False
    assert report["paper_timer_enable_authorized"] is False
    assert report["service_restart_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_resealed_report_cannot_authorize_manual_cycle():
    report = _report()
    report["manual_cycle_execution_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="manual_cycle_execution_authorized=false"):
        MODULE.validate_manual_cycle_readiness(report)


def test_resealed_report_cannot_enable_paper_timer():
    report = _report()
    report["paper_timer_enable_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_manual_cycle_readiness(report)


def test_resealed_report_cannot_hide_activation_observation():
    report = _report()
    report["activation_observed"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="must not observe activation"):
        MODULE.validate_manual_cycle_readiness(report)


def test_builder_requires_fresh_audit_exactly_equal_saved(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        production.mkdir()
        saved = _saved_audit(production)
        saved_path = root / "post-mutation.json"
        saved_path.write_text(json.dumps(saved), encoding="utf-8")

        class FakeAuditModule:
            @staticmethod
            def validate_post_mutation_audit(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_post_mutation_audit(**kwargs):
                return copy.deepcopy(saved)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_audit_module",
            lambda source: FakeAuditModule,
        )

        report = MODULE.build_manual_cycle_readiness(
            repository=production,
            source_tree=ROOT,
            private_bundle_report_path=root / "bundle.json",
            pre_mutation_handoff_path=root / "handoff.json",
            execution_precheck_path=root / "precheck.json",
            mutation_receipt_path=root / "receipt.json",
            post_mutation_audit_path=saved_path,
        )

    MODULE.validate_manual_cycle_readiness(report)
    assert report["fresh_post_mutation_audit_matches_saved"] is True
    assert report["manual_cycle_readiness_ready"] is True
    assert report["manual_cycle_execution_authorized"] is False


def test_builder_fails_closed_when_fresh_audit_drifts(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        production.mkdir()
        saved = _saved_audit(production)
        saved_path = root / "post-mutation.json"
        saved_path.write_text(json.dumps(saved), encoding="utf-8")

        fresh = copy.deepcopy(saved)
        fresh["post_mutation_audit_sha256"] = "9" * 64

        class FakeAuditModule:
            @staticmethod
            def validate_post_mutation_audit(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_post_mutation_audit(**kwargs):
                return copy.deepcopy(fresh)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_audit_module",
            lambda source: FakeAuditModule,
        )

        with pytest.raises(ValueError, match="differs from saved audit"):
            MODULE.build_manual_cycle_readiness(
                repository=production,
                source_tree=ROOT,
                private_bundle_report_path=root / "bundle.json",
                pre_mutation_handoff_path=root / "handoff.json",
                execution_precheck_path=root / "precheck.json",
                mutation_receipt_path=root / "receipt.json",
                post_mutation_audit_path=saved_path,
            )


def test_builder_fails_closed_if_activation_is_observed(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        production.mkdir()
        saved = _saved_audit(production)
        saved["activation_observed"] = True
        saved["activation_state_unchanged"] = False
        saved_path = root / "post-mutation.json"
        saved_path.write_text(json.dumps(saved), encoding="utf-8")

        class FakeAuditModule:
            @staticmethod
            def validate_post_mutation_audit(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_post_mutation_audit(**kwargs):
                return copy.deepcopy(saved)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_audit_module",
            lambda source: FakeAuditModule,
        )

        with pytest.raises(ValueError, match="activation_state_unchanged=true"):
            MODULE.build_manual_cycle_readiness(
                repository=production,
                source_tree=ROOT,
                private_bundle_report_path=root / "bundle.json",
                pre_mutation_handoff_path=root / "handoff.json",
                execution_precheck_path=root / "precheck.json",
                mutation_receipt_path=root / "receipt.json",
                post_mutation_audit_path=saved_path,
            )


def test_readiness_tool_has_no_activation_or_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "systemctl" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"manual_cycle_execution_authorized": False' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
