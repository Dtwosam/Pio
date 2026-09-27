from __future__ import annotations

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
    / "validate_manual_market_paper_research_store_portable_patch.py"
)

SPEC = importlib.util.spec_from_file_location(
    "validate_manual_market_paper_research_store_portable_patch",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_reviewed_research_store_portable_validation_dependency_is_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_apply_patch_works_in_temporary_non_git_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    target = workspace / "python-learner/src/meteora_learner/research_store.py"
    target.parent.mkdir(parents=True)
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")

    patch = tmp_path / "candidate.patch"
    patch.write_text(
        """--- a/python-learner/src/meteora_learner/research_store.py
+++ b/python-learner/src/meteora_learner/research_store.py
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
""",
        encoding="utf-8",
    )

    check_ok, apply_ok = MODULE._apply_patch(
        workspace=workspace,
        patch_file=patch,
    )

    assert check_ok is True
    assert apply_ok is True
    assert target.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_apply_patch_reports_failed_check_without_mutation(tmp_path):
    workspace = tmp_path / "workspace"
    target = workspace / "python-learner/src/meteora_learner/research_store.py"
    target.parent.mkdir(parents=True)
    target.write_text("different\n", encoding="utf-8")

    patch = tmp_path / "candidate.patch"
    patch.write_text(
        """--- a/python-learner/src/meteora_learner/research_store.py
+++ b/python-learner/src/meteora_learner/research_store.py
@@ -1 +1 @@
-expected
+candidate
""",
        encoding="utf-8",
    )

    check_ok, apply_ok = MODULE._apply_patch(
        workspace=workspace,
        patch_file=patch,
    )

    assert check_ok is False
    assert apply_ok is False
    assert target.read_text(encoding="utf-8") == "different\n"


def test_patch_input_symlink_is_rejected(tmp_path):
    target = tmp_path / "candidate.patch"
    target.write_text("patch\n", encoding="utf-8")
    link = tmp_path / "candidate-link.patch"
    link.symlink_to(target)

    try:
        MODULE._resolve_regular_file(
            link,
            label="research-store portable patch",
        )
    except ValueError as exc:
        assert "must not be a symlink" in str(exc)
    else:
        raise AssertionError("symlink research-store patch input must fail closed")


def test_python_discovery_accepts_explicit_existing_executable():
    resolved = MODULE._resolve_python(sys.executable)

    assert resolved == str(Path(sys.executable).resolve())


def test_validation_env_prioritizes_temporary_workspace_source(tmp_path, monkeypatch):
    python_dir = tmp_path / "python-learner"
    monkeypatch.setenv("PYTHONPATH", "/existing/path")

    env = MODULE._validation_env(python_dir)

    expected = str(python_dir / "src")
    assert env["PYTHONPATH"].split(__import__("os").pathsep)[0] == expected
    assert "/existing/path" in env["PYTHONPATH"].split(__import__("os").pathsep)[1:]


def test_pytest_probe_succeeds_in_test_environment(tmp_path):
    assert MODULE._probe_pytest(
        python_executable=str(Path(sys.executable).resolve()),
        cwd=tmp_path,
        env=dict(__import__("os").environ),
    ) is True


def _command(name, args, *, python="/usr/bin/python3", returncode=0):
    stdout = b"ok\n"
    stderr = b""
    return {
        "name": name,
        "argv": [python, *args],
        "returncode": returncode,
        "passed": returncode == 0,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stdout_size": len(stdout),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "stderr_size": len(stderr),
    }


def _synthetic_report(
    *,
    apply_check=True,
    reconstructed=True,
    python_available=True,
    pytest_available=True,
    command_returncode=0,
):
    python = "/usr/bin/python3" if python_available else None
    commands = [
        _command(
            name,
            args,
            python=python or "python",
            returncode=(
                command_returncode
                if python_available and pytest_available
                else 125
            ),
        )
        for name, args in MODULE.COMMANDS
    ]

    all_passed = all(item["passed"] for item in commands)
    ready = (
        apply_check
        and reconstructed
        and python_available
        and pytest_available
        and all_passed
    )
    if ready:
        blocker = None
    elif not apply_check:
        blocker = "PATCH_APPLY_FAILED"
    elif not reconstructed:
        blocker = "CANDIDATE_RECONSTRUCTION_MISMATCH"
    elif not python_available:
        blocker = "PYTHON_NOT_FOUND"
    elif not pytest_available:
        blocker = "PYTEST_NOT_AVAILABLE"
    else:
        blocker = "COMMAND_FAILED"

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
        "patch_report_sha256": "2" * 64,
        "patch_sha256": "3" * 64,
        "patch_size": 123,
        "candidate_git_blob": MODULE.EXPECTED_CANDIDATE_BLOB,
        "candidate_sha256": "5" * 64,
        "candidate_size": 456,
        "patch_file": "/private/tmp/research-store-candidate.patch",
        "workspace_under_var_tmp": True,
        "workspace_source_precedence": True,
        "python_executable": python,
        "python_available": python_available,
        "pytest_available": pytest_available,
        "validation_blocker": blocker,
        "apply_check_passed": apply_check,
        "candidate_reconstruction_passed": reconstructed,
        "validation_commands": commands,
        "all_commands_passed": all_passed,
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
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_portable_validation_accepts_ready_sealed_report():
    report = _synthetic_report()

    MODULE.validate_portable_validation_report(
        json.loads(json.dumps(report))
    )

    assert report["validation_ready"] is True
    assert report["validation_blocker"] is None
    assert report["mutation_authorized"] is False


def test_portable_validation_accepts_pytest_unavailable_blocker():
    report = _synthetic_report(pytest_available=False)

    MODULE.validate_portable_validation_report(
        json.loads(json.dumps(report))
    )

    assert report["validation_ready"] is False
    assert report["validation_blocker"] == "PYTEST_NOT_AVAILABLE"


def test_portable_validation_accepts_patch_apply_blocker():
    report = _synthetic_report(apply_check=False)

    MODULE.validate_portable_validation_report(
        json.loads(json.dumps(report))
    )

    assert report["validation_ready"] is False
    assert report["validation_blocker"] == "PATCH_APPLY_FAILED"


def test_portable_validation_accepts_command_failure():
    report = _synthetic_report(command_returncode=1)

    MODULE.validate_portable_validation_report(
        json.loads(json.dumps(report))
    )

    assert report["validation_ready"] is False
    assert report["validation_blocker"] == "COMMAND_FAILED"


def test_rehashed_portable_validation_cannot_claim_other_candidate_blob():
    report = _synthetic_report()
    report["candidate_git_blob"] = "4" * 40
    identity = {
        field: report[field]
        for field in MODULE.IDENTITY_FIELDS
    }
    report["report_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_portable_validation_report(report)
    except ValueError as exc:
        assert "candidate blob mismatch" in str(exc)
    else:
        raise AssertionError("rehashed candidate substitution must fail closed")


def test_rehashed_portable_validation_cannot_authorize_mutation():
    report = _synthetic_report()
    report["mutation_authorized"] = True
    identity = {
        field: report[field]
        for field in MODULE.IDENTITY_FIELDS
    }
    report["report_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_portable_validation_report(report)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_portable_validator_is_production_blind_and_installs_nothing():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "systemctl" not in source
    assert '"--patch-file"' in source
    assert '"--patch-report"' in source
    assert '"--python-bin"' in source
    assert "PYTHONPATH" in source
    assert "_validation_env" in source
    assert 'git", "apply"' in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "pip install" not in source
    assert "uv sync" not in source
    assert "poetry install" not in source
    assert "apply=True" not in source
