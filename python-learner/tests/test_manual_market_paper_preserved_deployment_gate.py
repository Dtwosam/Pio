from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_manual_market_paper_preserved_deployment_gate.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_preserved_deployment_gate",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _sealed_gate(**overrides):
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
        "saved_handoff_sha256": "1" * 64,
        "saved_handoff_state_sha256": "2" * 64,
        "saved_plan_sha256": "3" * 64,
        "private_bundle_sha256": "4" * 64,
        "current_readiness_sha256": "5" * 64,
        "snapshot_handoff_ready": True,
        "current_handoff_ready": True,
        "snapshot_state_sha256": "2" * 64,
        "current_state_sha256": "2" * 64,
        "state_matches": True,
        "changed_sections": [],
        "rebuilt_plan_matches": True,
        "plan_ready": True,
        "plan_mutation_authorized": False,
        "operation_count": 2,
        "deployment_needed": True,
        "gate_ready": True,
        "requires_fresh_mutation_review": True,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    identity.update(overrides)
    return {
        **identity,
        "gate_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_reviewed_preserved_gate_dependencies_are_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_sealed_preserved_gate_validates():
    report = _sealed_gate()

    MODULE.validate_preserved_gate(json.loads(json.dumps(report)))

    assert report["gate_ready"] is True
    assert report["state_matches"] is True
    assert report["mutation_authorized"] is False


def test_rehashed_preserved_gate_cannot_authorize_mutation():
    report = _sealed_gate(mutation_authorized=True)

    try:
        MODULE.validate_preserved_gate(report)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_matching_state_cannot_report_changed_sections_even_if_resealed():
    report = _sealed_gate(changed_sections=["tracked_diff_sha256"])

    try:
        MODULE.validate_preserved_gate(report)
    except ValueError as exc:
        assert "cannot report changes" in str(exc)
    else:
        raise AssertionError("contradictory changed sections must fail closed")


def test_drifted_state_requires_changed_sections():
    report = _sealed_gate(
        current_state_sha256="6" * 64,
        state_matches=False,
        changed_sections=[],
        gate_ready=False,
    )

    try:
        MODULE.validate_preserved_gate(report)
    except ValueError as exc:
        assert "must report changes" in str(exc)
    else:
        raise AssertionError("drift without changed section must fail closed")


def test_build_gate_derives_account_and_pool_from_saved_handoff(
    tmp_path,
    monkeypatch,
):
    bundle_sha = "4" * 64
    handoff_sha = "1" * 64
    state_sha = "2" * 64
    plan_sha = "3" * 64
    current_readiness_sha = "5" * 64

    saved_handoff = {
        "handoff_ready": True,
        "handoff_sha256": handoff_sha,
        "production_state_sha256": state_sha,
        "state": {
            "private_bundle_sha256": bundle_sha,
            "paper_account": "sealed-paper-account",
            "target_pool": "sealed-target-pool",
        },
    }
    saved_plan = {
        "plan_ready": True,
        "mutation_authorized": False,
        "handoff_sha256": handoff_sha,
        "handoff_state_sha256": state_sha,
        "private_bundle_sha256": bundle_sha,
        "plan_sha256": plan_sha,
        "operation_count": 1,
        "deployment_needed": True,
    }
    bundle_report = {"bundle_sha256": bundle_sha}
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle_report), encoding="utf-8")

    readiness_module = object()

    def validate_handoff(snapshot):
        assert snapshot is saved_handoff

    def build_handoff_snapshot(
        current_report,
        *,
        tracked_diff_sha256,
        readiness_module: object,
    ):
        assert tracked_diff_sha256 == "f" * 64
        return {
            "handoff_ready": True,
            "production_state_sha256": state_sha,
        }

    def compare_handoff_snapshot(
        snapshot,
        current_report,
        *,
        tracked_diff_sha256,
        readiness_module: object,
    ):
        assert snapshot is saved_handoff
        assert tracked_diff_sha256 == "f" * 64
        return {
            "state_matches": True,
            "changed_sections": [],
        }

    def build_current_report(
        *,
        repository,
        source_tree,
        private_bundle_report,
        pool,
        paper_account,
    ):
        assert repository.endswith("production")
        assert pool == "sealed-target-pool"
        assert paper_account == "sealed-paper-account"
        assert Path(private_bundle_report) == bundle_path.resolve()
        return (
            {"readiness_sha256": current_readiness_sha},
            "f" * 64,
            readiness_module,
        )

    fake_handoff = SimpleNamespace(
        validate_handoff_snapshot=validate_handoff,
        build_handoff_snapshot=build_handoff_snapshot,
        compare_handoff_snapshot=compare_handoff_snapshot,
        _build_current_report=build_current_report,
    )

    def validate_plan(plan):
        assert plan is saved_plan

    def rebuild_plan(**kwargs):
        assert kwargs["handoff_snapshot"] is saved_handoff
        assert kwargs["private_bundle_report"] == bundle_report
        return saved_plan

    fake_plan = SimpleNamespace(
        validate_preserved_deployment_plan=validate_plan,
        build_preserved_deployment_plan=rebuild_plan,
    )

    monkeypatch.setattr(MODULE, "_verify_reviewed_source", lambda source: None)

    def load_module(path, name):
        if Path(path).name == MODULE.HANDOFF_TOOL.name:
            return fake_handoff
        if Path(path).name == MODULE.PLAN_TOOL.name:
            return fake_plan
        raise AssertionError(f"unexpected module path: {path}")

    monkeypatch.setattr(MODULE, "_load_module", load_module)

    source = tmp_path / "source"
    source.mkdir()
    production = tmp_path / "production"
    production.mkdir()

    report = MODULE.build_preserved_gate(
        repository=production,
        source_tree=source,
        private_bundle_report_path=bundle_path,
        saved_handoff=saved_handoff,
        saved_plan=saved_plan,
    )

    assert report["gate_ready"] is True
    assert report["private_bundle_sha256"] == bundle_sha
    assert report["current_readiness_sha256"] == current_readiness_sha
    assert report["mutation_authorized"] is False


def test_preserved_gate_cli_has_no_account_or_pool_override():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--paper-account"' not in source
    assert 'parser.add_argument("--pool"' not in source


def test_preserved_gate_has_no_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert "copy2(" not in source
    assert ".unlink(" not in source
    assert ".rename(" not in source
    assert "systemctl" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
