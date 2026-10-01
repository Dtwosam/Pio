from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase8_recursive_reentry_checkpoint_v25_artifact_status.py"
)
SPEC = importlib.util.spec_from_file_location(
    "check_phase8_recursive_reentry_checkpoint_v25_artifact_status",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _manifest():
    value, _ = MODULE._load_manifest(ROOT)
    return value


def _write_stage(directory: Path, step: dict, value=None) -> Path:
    path = directory / step["output_artifact"]
    path.write_text(
        json.dumps({} if value is None else value),
        encoding="utf-8",
    )
    return path


def _fake_validator_loader(failing_role: str | None = None):
    def load(source, step):
        assert source == ROOT

        def validate(value):
            assert isinstance(value, dict)
            if step["role"] == failing_role:
                raise ValueError("sensitive internal rejection detail")

        return validate

    return load


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["artifact_status_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_manifest_and_all_reviewed_tools_are_exactly_pinned():
    manifest, manifest_sha = MODULE._load_manifest(ROOT)
    assert len(manifest_sha) == 64
    blobs = MODULE._verify_source_tools(ROOT, manifest)
    assert len(blobs) == 8
    assert all(len(value) == 40 for value in blobs.values())


def test_all_native_validators_are_loadable():
    manifest = _manifest()
    for step in manifest["ordered_steps"]:
        validator = MODULE._load_validator(ROOT, step)
        assert callable(validator)


def test_empty_directory_reports_first_read_only_artifact(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_validator",
        _fake_validator_loader(),
    )
    with tempfile.TemporaryDirectory() as temp:
        report = MODULE.build_phase8_recursive_reentry_checkpoint_v25_artifact_status(
            source_tree=ROOT,
            artifact_directory=temp,
        )

    assert report["valid_prefix_length"] == 0
    assert report["artifact_chain_complete"] is False
    assert report["first_incomplete_order"] == 1
    assert report["first_incomplete_role"] == "bundled-canonical-checkpoint"
    assert report["first_incomplete_status"] == "MISSING"
    assert report["next_boundary"] == "READ_ONLY_ARTIFACT_REQUIRED"
    assert report["out_of_order_artifacts_present"] is False
    assert report["next_action_authorized"] is False
    assert report["paper_supervisor_tick_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_five_valid_stages_stop_at_mutation_review(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_validator",
        _fake_validator_loader(),
    )
    manifest = _manifest()
    with tempfile.TemporaryDirectory() as temp:
        artifact_dir = Path(temp)
        for step in manifest["ordered_steps"][:5]:
            _write_stage(artifact_dir, step)
        report = MODULE.build_phase8_recursive_reentry_checkpoint_v25_artifact_status(
            source_tree=ROOT,
            artifact_directory=artifact_dir,
        )

    assert report["valid_prefix_length"] == 5
    assert report["first_incomplete_role"] == "one-shot-paper-executor"
    assert report["next_boundary"] == "MUTATION_BOUNDARY_REVIEW_REQUIRED"
    assert report["next_action_authorized"] is False
    assert report["paper_supervisor_tick_authorized"] is False


def test_missing_signature_with_later_artifact_reports_order_review(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_validator",
        _fake_validator_loader(),
    )
    manifest = _manifest()
    with tempfile.TemporaryDirectory() as temp:
        artifact_dir = Path(temp)
        for step in manifest["ordered_steps"][:3]:
            _write_stage(artifact_dir, step)
        _write_stage(artifact_dir, manifest["ordered_steps"][4])
        report = MODULE.build_phase8_recursive_reentry_checkpoint_v25_artifact_status(
            source_tree=ROOT,
            artifact_directory=artifact_dir,
        )

    assert report["valid_prefix_length"] == 3
    assert report["first_incomplete_role"] == "detached-authorization"
    assert report["out_of_order_artifacts_present"] is True
    assert report["next_boundary"] == "ARTIFACT_ORDER_REVIEW_REQUIRED"


def test_validator_rejection_is_categorical_and_non_leaking(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_validator",
        _fake_validator_loader("tick-request"),
    )
    manifest = _manifest()
    with tempfile.TemporaryDirectory() as temp:
        artifact_dir = Path(temp)
        for step in manifest["ordered_steps"][:3]:
            _write_stage(artifact_dir, step)
        report = MODULE.build_phase8_recursive_reentry_checkpoint_v25_artifact_status(
            source_tree=ROOT,
            artifact_directory=artifact_dir,
        )

    stage = report["stages"][2]
    assert stage["validation_status"] == "INVALID"
    assert stage["validation_error_category"] == "VALIDATOR_REJECTED"
    assert "sensitive internal rejection detail" not in json.dumps(report)
    assert report["invalid_artifact_present"] is True
    assert report["next_boundary"] == "INVALID_ARTIFACT_REVIEW_REQUIRED"


def test_symlinked_artifact_is_unsafe(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_validator",
        _fake_validator_loader(),
    )
    manifest = _manifest()
    with tempfile.TemporaryDirectory() as temp:
        artifact_dir = Path(temp)
        actual = artifact_dir / "actual.json"
        actual.write_text("{}", encoding="utf-8")
        first = artifact_dir / manifest["ordered_steps"][0]["output_artifact"]
        first.symlink_to(actual)
        report = MODULE.build_phase8_recursive_reentry_checkpoint_v25_artifact_status(
            source_tree=ROOT,
            artifact_directory=artifact_dir,
        )

    assert report["stages"][0]["validation_status"] == "UNSAFE"
    assert report["stages"][0]["regular_file"] is False
    assert report["invalid_artifact_present"] is True
    assert report["next_boundary"] == "INVALID_ARTIFACT_REVIEW_REQUIRED"


def test_complete_saved_chain_is_status_only(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_validator",
        _fake_validator_loader(),
    )
    manifest = _manifest()
    with tempfile.TemporaryDirectory() as temp:
        artifact_dir = Path(temp)
        for step in manifest["ordered_steps"]:
            _write_stage(artifact_dir, step)
        report = MODULE.build_phase8_recursive_reentry_checkpoint_v25_artifact_status(
            source_tree=ROOT,
            artifact_directory=artifact_dir,
        )

    assert report["valid_prefix_length"] == 8
    assert report["artifact_chain_complete"] is True
    assert report["first_incomplete_role"] is None
    assert report["next_boundary"] == "EVIDENCE_CHAIN_COMPLETE"
    assert report["status_only"] is True
    assert report["fresh_preflight_required_before_any_operator_sequence"] is True
    assert report["detached_signature_reverification_performed"] is False
    assert report["production_database_revalidation_performed"] is False
    assert report["next_action_authorized"] is False
    assert report["phase8_promotion_authorized"] is False
    MODULE.validate_phase8_recursive_reentry_checkpoint_v25_artifact_status(
        report
    )


def test_resealed_status_cannot_authorize_next_action(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_validator",
        _fake_validator_loader(),
    )
    with tempfile.TemporaryDirectory() as temp:
        report = MODULE.build_phase8_recursive_reentry_checkpoint_v25_artifact_status(
            source_tree=ROOT,
            artifact_directory=temp,
        )

    report["next_action_authorized"] = True
    _reseal(report)
    with pytest.raises(ValueError, match="next_action_authorized=false"):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_artifact_status(
            report
        )


def test_status_tool_has_no_execution_or_database_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "sqlite3.connect" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert "subprocess.run" not in source
    assert "os.system" not in source
    assert (
        "execute_phase8_recursive_reentry_checkpoint_v25_"
        "continuation_evidence_tick_once("
    ) not in source
    assert '"next_action_authorized": False' in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"phase8_promotion_authorized": False' in source
