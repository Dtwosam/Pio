from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_preservation_review.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_preservation_review",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _load(relative: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RESEARCH = _load(
    MODULE.RESEARCH_VALIDATOR,
    "preservation_review_test_research_validator",
)
STATE = _load(
    MODULE.STATE_VALIDATOR,
    "preservation_review_test_state_validator",
)


def _command(
    *,
    name: str,
    argv: list[str],
    returncode: int,
):
    stdout = b"ok\n" if returncode == 0 else b""
    stderr = b"" if returncode == 0 else b"blocked\n"
    return {
        "name": name,
        "argv": argv,
        "returncode": returncode,
        "passed": returncode == 0,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stdout_size": len(stdout),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "stderr_size": len(stderr),
    }


def _state_report(*, head="1" * 40, ready=True):
    cargo = "/usr/bin/cargo" if ready else None
    commands = [
        _command(
            name=name,
            argv=[cargo if ready else "cargo", *args],
            returncode=0 if ready else 127,
        )
        for name, args in STATE.COMMANDS
    ]
    identity = {
        "format_version": STATE.FORMAT_VERSION,
        "artifact_type": STATE.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                STATE.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "reviewed_source_head": head,
        "patch_report_sha256": "2" * 64,
        "patch_sha256": "3" * 64,
        "patch_size": 4096,
        "candidate_git_blob": STATE.EXPECTED_CANDIDATE_BLOB,
        "candidate_sha256": "4" * 64,
        "candidate_size": 31806,
        "patch_file": "/private/tmp/state-reader.patch",
        "workspace_under_var_tmp": True,
        "cargo_executable": cargo,
        "cargo_available": ready,
        "validation_blocker": None if ready else "CARGO_NOT_FOUND",
        "apply_check_passed": True,
        "candidate_reconstruction_passed": True,
        "validation_commands": commands,
        "all_commands_passed": ready,
        "validation_ready": ready,
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
            STATE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _research_report(*, head="1" * 40, ready=True):
    python = "/usr/bin/python3"
    commands = [
        _command(
            name=name,
            argv=[python, *args],
            returncode=0 if ready else 125,
        )
        for name, args in RESEARCH.COMMANDS
    ]
    identity = {
        "format_version": RESEARCH.FORMAT_VERSION,
        "artifact_type": RESEARCH.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                RESEARCH.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "reviewed_source_head": head,
        "patch_report_sha256": "5" * 64,
        "patch_sha256": "6" * 64,
        "patch_size": 4096,
        "candidate_git_blob": RESEARCH.EXPECTED_CANDIDATE_BLOB,
        "candidate_sha256": "7" * 64,
        "candidate_size": 32000,
        "patch_file": "/private/tmp/research-store.patch",
        "workspace_under_var_tmp": True,
        "workspace_source_precedence": True,
        "python_executable": python,
        "python_available": True,
        "pytest_available": ready,
        "validation_blocker": None if ready else "PYTEST_NOT_AVAILABLE",
        "apply_check_passed": True,
        "candidate_reconstruction_passed": True,
        "validation_commands": commands,
        "all_commands_passed": ready,
        "validation_ready": ready,
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
            RESEARCH._canonical_bytes(identity)
        ).hexdigest(),
    }


def _write_json(tmp_path: Path, name: str, value: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_reviewed_preservation_validators_are_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_ready_validations_produce_sealed_two_file_preservation_review(tmp_path):
    research_path = _write_json(
        tmp_path,
        "research.json",
        _research_report(),
    )
    state_path = _write_json(
        tmp_path,
        "state.json",
        _state_report(),
    )

    review = MODULE.build_preservation_review(
        source_tree=ROOT,
        research_store_validation=research_path,
        state_reader_validation=state_path,
    )

    MODULE.validate_preservation_review(
        json.loads(json.dumps(review))
    )
    assert review["preservation_ready"] is True
    assert review["blockers"] == []
    assert review["validation_source_heads_match"] is True
    assert review["entry_count"] == 2
    assert [entry["preserved_candidate_blob"] for entry in review["entries"]] == [
        "31bd88e3d74490f5d0b617ff4b36383e7e12e18f",
        "f54a1021cf8f89d285bde957d1f72d81857ec2fa",
    ]
    assert [
        operation["expected_production_current_blob"]
        for operation in review["proposed_rebase_operations"]
    ] == [
        "f9deb47c10c88a4e1e364dd12d6c7569c3826a98",
        "d1267db6708b91bc8cacabffcd397a380866c79a",
    ]
    assert review["candidate_content_included"] is False
    assert review["production_file_modified"] is False
    assert review["source_tree_modified"] is False
    assert review["requires_new_readiness_cycle"] is True
    assert review["mutation_authorized"] is False


def test_blocked_validation_is_preserved_as_nonready_evidence(tmp_path):
    research_path = _write_json(
        tmp_path,
        "research.json",
        _research_report(ready=False),
    )
    state_path = _write_json(
        tmp_path,
        "state.json",
        _state_report(),
    )

    review = MODULE.build_preservation_review(
        source_tree=ROOT,
        research_store_validation=research_path,
        state_reader_validation=state_path,
    )

    MODULE.validate_preservation_review(review)
    assert review["preservation_ready"] is False
    assert review["blockers"] == ["RESEARCH_STORE:PYTEST_NOT_AVAILABLE"]
    assert review["entries"][0]["entry_ready"] is False
    assert review["entries"][1]["entry_ready"] is True


def test_validation_source_head_mismatch_blocks_preservation(tmp_path):
    research_path = _write_json(
        tmp_path,
        "research.json",
        _research_report(head="1" * 40),
    )
    state_path = _write_json(
        tmp_path,
        "state.json",
        _state_report(head="2" * 40),
    )

    review = MODULE.build_preservation_review(
        source_tree=ROOT,
        research_store_validation=research_path,
        state_reader_validation=state_path,
    )

    MODULE.validate_preservation_review(review)
    assert review["preservation_ready"] is False
    assert review["validation_source_heads_match"] is False
    assert review["blockers"] == ["VALIDATION_SOURCE_HEAD_MISMATCH"]


def test_rehashed_validation_cannot_substitute_other_candidate_blob(tmp_path):
    state = _state_report()
    state["candidate_git_blob"] = "9" * 40
    identity = {
        field: state[field]
        for field in STATE.IDENTITY_FIELDS
    }
    state["report_sha256"] = hashlib.sha256(
        STATE._canonical_bytes(identity)
    ).hexdigest()

    research_path = _write_json(
        tmp_path,
        "research.json",
        _research_report(),
    )
    state_path = _write_json(tmp_path, "state.json", state)

    try:
        MODULE.build_preservation_review(
            source_tree=ROOT,
            research_store_validation=research_path,
            state_reader_validation=state_path,
        )
    except ValueError as exc:
        assert "candidate blob mismatch" in str(exc)
    else:
        raise AssertionError("candidate substitution must fail closed")


def test_tampered_preservation_review_fails_digest_validation(tmp_path):
    research_path = _write_json(
        tmp_path,
        "research.json",
        _research_report(),
    )
    state_path = _write_json(
        tmp_path,
        "state.json",
        _state_report(),
    )
    review = MODULE.build_preservation_review(
        source_tree=ROOT,
        research_store_validation=research_path,
        state_reader_validation=state_path,
    )

    tampered = copy.deepcopy(review)
    tampered["entries"][0]["candidate_size"] += 1

    try:
        MODULE.validate_preservation_review(tampered)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered preservation review must fail closed")


def test_rehashed_preservation_review_cannot_authorize_mutation(tmp_path):
    research_path = _write_json(
        tmp_path,
        "research.json",
        _research_report(),
    )
    state_path = _write_json(
        tmp_path,
        "state.json",
        _state_report(),
    )
    review = MODULE.build_preservation_review(
        source_tree=ROOT,
        research_store_validation=research_path,
        state_reader_validation=state_path,
    )

    review["mutation_authorized"] = True
    identity = {
        field: review[field]
        for field in MODULE.REVIEW_FIELDS
    }
    review["review_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_preservation_review(review)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_preservation_review_is_evidence_only_and_has_no_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "/opt/pio" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert "subprocess" not in source
    assert "shutil" not in source
    assert "systemctl" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
