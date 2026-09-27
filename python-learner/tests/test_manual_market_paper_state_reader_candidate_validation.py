from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "validate_manual_market_paper_state_reader_candidate.py"
)

SPEC = importlib.util.spec_from_file_location(
    "validate_manual_market_paper_state_reader_candidate",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

BUILDER_SPEC = importlib.util.spec_from_file_location(
    "validate_candidate_test_builder",
    ROOT / MODULE.BUILDER_TOOL,
)
assert BUILDER_SPEC is not None and BUILDER_SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(BUILDER_SPEC)
sys.modules[BUILDER_SPEC.name] = BUILDER
BUILDER_SPEC.loader.exec_module(BUILDER)


def test_reviewed_validation_dependency_is_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_command_result_records_only_exit_and_output_hash_metadata():
    result = MODULE._command_result(
        name="probe",
        command=(
            sys.executable,
            "-c",
            "import sys; print('ok'); print('warn', file=sys.stderr)",
        ),
        cwd=ROOT,
        env=dict(__import__("os").environ),
    )

    assert result["name"] == "probe"
    assert result["passed"] is True
    assert result["returncode"] == 0
    assert len(result["stdout_sha256"]) == 64
    assert len(result["stderr_sha256"]) == 64
    assert result["stdout_size"] > 0
    assert result["stderr_size"] > 0
    serialized = json.dumps(result)
    assert '"ok"' not in serialized
    assert '"warn"' not in serialized


def _validation_command(name, argv, *, returncode=0):
    stdout = b"ok\n"
    stderr = b""
    return {
        "name": name,
        "argv": list(argv),
        "returncode": returncode,
        "passed": returncode == 0,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stdout_size": len(stdout),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "stderr_size": len(stderr),
    }


def _synthetic_validation_report(*, second_returncode=0):
    commands = [
        _validation_command(
            MODULE.COMMANDS[0][0],
            MODULE.COMMANDS[0][1],
        ),
        _validation_command(
            MODULE.COMMANDS[1][0],
            MODULE.COMMANDS[1][1],
            returncode=second_returncode,
        ),
    ]
    all_passed = all(item["passed"] for item in commands)

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
        "reviewed_source_head": "1" * 40,
        "candidate_report_sha256": "2" * 64,
        "candidate_git_blob": "3" * 40,
        "candidate_sha256": "4" * 64,
        "candidate_size": 123,
        "candidate_path": "/var/tmp/pio-state-reader-candidate.rs",
        "validation_workspace_under_var_tmp": True,
        "validation_commands": commands,
        "all_commands_passed": all_passed,
        "validation_ready": all_passed,
        "production_file_modified": False,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    return {
        **identity,
        "report_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_validation_report_accepts_successful_sealed_result():
    report = _synthetic_validation_report()

    MODULE.validate_validation_report(
        json.loads(json.dumps(report))
    )

    assert report["all_commands_passed"] is True
    assert report["validation_ready"] is True
    assert report["production_file_modified"] is False
    assert report["mutation_authorized"] is False


def test_validation_report_accepts_failed_test_result_but_is_not_ready():
    report = _synthetic_validation_report(second_returncode=101)

    MODULE.validate_validation_report(
        json.loads(json.dumps(report))
    )

    assert report["all_commands_passed"] is False
    assert report["validation_ready"] is False


def test_tampered_validation_report_fails_digest_validation():
    report = _synthetic_validation_report()
    report["candidate_size"] += 1

    try:
        MODULE.validate_validation_report(report)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered validation report must fail closed")


def test_rehashed_validation_report_cannot_authorize_mutation():
    report = _synthetic_validation_report()
    report["mutation_authorized"] = True
    identity = {
        field: report[field]
        for field in MODULE.IDENTITY_FIELDS
    }
    report["report_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_validation_report(report)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_rehashed_validation_report_cannot_escape_var_tmp():
    report = _synthetic_validation_report()
    report["candidate_path"] = "/opt/pio/rust-executor/src/state_reader.rs"
    identity = {
        field: report[field]
        for field in MODULE.IDENTITY_FIELDS
    }
    report["report_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_validation_report(report)
    except ValueError as exc:
        assert "must remain under /var/tmp" in str(exc)
    else:
        raise AssertionError("candidate path escape must fail closed")


def test_validator_has_no_production_repository_argument_or_mutation_path():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "/opt/pio" not in source
    assert "TemporaryDirectory" in source
    assert 'dir="/var/tmp"' in source
    assert "CARGO_BUILD_JOBS" in source
    assert 'env["CARGO_BUILD_JOBS"] = "1"' in source

    assert source.count(".write_bytes(") == 1
    assert "target_path.write_bytes(candidate)" in source
    assert "systemctl" not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source


def test_builder_report_path_binding_rejects_rehashed_redirect():
    candidate = b"candidate\n"
    current = b"current\n"
    target = b"target\n"
    identity = {
        "format_version": BUILDER.FORMAT_VERSION,
        "artifact_type": BUILDER.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                BUILDER.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "repository": "/opt/pio",
        "production_head": BUILDER.EXPECTED_PRODUCTION_HEAD,
        "source_hunk_evidence_sha256": BUILDER.EXPECTED_HUNK_EVIDENCE_SHA256,
        "path": "rust-executor/src/main.rs",
        "current_blob": BUILDER.EXPECTED_CURRENT_BLOB,
        "target_blob": BUILDER.EXPECTED_TARGET_BLOB,
        "resolutions": [
            {
                "index": item["index"],
                "conflict_sha256": item["conflict_sha256"],
                "resolution": "REVIEWED_TARGET",
            }
            for item in BUILDER.EXPECTED_CONFLICTS
        ],
        "candidate_git_blob": BUILDER._git_blob_sha_bytes(candidate),
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "candidate_size": len(candidate),
        "candidate_diff_from_current": BUILDER._unified_patch_stats(
            current,
            candidate,
        ),
        "candidate_diff_from_target": BUILDER._unified_patch_stats(
            target,
            candidate,
        ),
        "output_path": "/var/tmp/candidate.rs",
        "output_under_var_tmp": True,
        "production_file_modified": False,
        "candidate_requires_isolated_validation": True,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    report = {
        **identity,
        "report_sha256": hashlib.sha256(
            BUILDER._canonical_bytes(identity)
        ).hexdigest(),
    }

    try:
        BUILDER.validate_candidate_report(report)
    except ValueError as exc:
        assert "path scope mismatch" in str(exc)
    else:
        raise AssertionError("builder path redirect must fail closed")
