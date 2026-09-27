from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_RESEARCH_STORE_PORTABLE_VALIDATION_V1"

EXPORTER_TOOL = Path(
    "deploy/tools/export_manual_market_paper_research_store_candidate_patch.py"
)
RESEARCH_STORE_PATH = Path(
    "python-learner/src/meteora_learner/research_store.py"
)

REVIEWED_SOURCE_BLOBS = {
    EXPORTER_TOOL: "1c70ecdd49005fa55bc381dc9c40e705e6c9b508",
}

EXPECTED_CANDIDATE_BLOB = "31bd88e3d74490f5d0b617ff4b36383e7e12e18f"

COMMANDS = (
    (
        "py_compile",
        ("-m", "py_compile", "src/meteora_learner/research_store.py"),
    ),
    (
        "pytest",
        ("-m", "pytest", "-q"),
    ),
)

IDENTITY_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "reviewed_source_head",
    "patch_report_sha256",
    "patch_sha256",
    "patch_size",
    "candidate_git_blob",
    "candidate_sha256",
    "candidate_size",
    "patch_file",
    "workspace_under_var_tmp",
    "workspace_source_precedence",
    "python_executable",
    "python_available",
    "pytest_available",
    "validation_blocker",
    "apply_check_passed",
    "candidate_reconstruction_passed",
    "validation_commands",
    "all_commands_passed",
    "validation_ready",
    "production_file_modified",
    "requires_separate_mutation_authorization",
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _git_blob_sha_bytes(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _git_blob_sha(path: Path) -> str:
    return _git_blob_sha_bytes(path.read_bytes())


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_hex_digest(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _verify_reviewed_source(source: Path) -> None:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if not path.is_file():
            raise ValueError(
                f"reviewed research-store portable-validation artifact is missing: {relative}"
            )
        if path.is_symlink():
            raise ValueError(
                f"reviewed research-store portable-validation artifact is a symlink: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"reviewed research-store portable-validation artifact mismatch: {relative}"
            )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _reviewed_source_head(source: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(source),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError("cannot read reviewed source HEAD")
    head = proc.stdout.strip()
    if not _is_hex_digest(head, 40):
        raise ValueError("reviewed source HEAD is invalid")
    return head


def _resolve_python(python_bin: str | None) -> str | None:
    if python_bin:
        raw = Path(python_bin).expanduser()
        if raw.is_absolute() or "/" in python_bin:
            candidates = [raw]
        else:
            resolved = shutil.which(python_bin)
            candidates = [Path(resolved)] if resolved else []
    else:
        candidates = [Path(sys.executable)]
        for name in ("python3", "python"):
            resolved = shutil.which(name)
            if resolved:
                candidates.append(Path(resolved))

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved_path = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError):
            continue
        key = str(resolved_path)
        if key in seen:
            continue
        seen.add(key)
        if resolved_path.is_file() and os.access(resolved_path, os.X_OK):
            return key
    return None


def _validation_env(python_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    workspace_src = python_dir / "src"
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(workspace_src)
        if not existing
        else str(workspace_src) + os.pathsep + existing
    )
    return env


def _probe_pytest(
    *,
    python_executable: str,
    cwd: Path,
    env: dict[str, str],
) -> bool:
    proc = subprocess.run(
        [python_executable, "-c", "import pytest"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def _command_result(
    *,
    name: str,
    command: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            list(command),
            cwd=str(cwd),
            env=env,
            capture_output=True,
            check=False,
        )
        stdout = (
            proc.stdout
            if isinstance(proc.stdout, bytes)
            else str(proc.stdout).encode()
        )
        stderr = (
            proc.stderr
            if isinstance(proc.stderr, bytes)
            else str(proc.stderr).encode()
        )
        returncode = int(proc.returncode)
    except FileNotFoundError:
        stdout = b""
        stderr = b"EXECUTABLE_NOT_FOUND\n"
        returncode = 127
    except PermissionError:
        stdout = b""
        stderr = b"EXECUTABLE_NOT_EXECUTABLE\n"
        returncode = 126
    except OSError:
        stdout = b""
        stderr = b"EXECUTION_OS_ERROR\n"
        returncode = 125

    return {
        "name": name,
        "argv": list(command),
        "returncode": returncode,
        "passed": returncode == 0,
        "stdout_sha256": _sha256_bytes(stdout),
        "stdout_size": len(stdout),
        "stderr_sha256": _sha256_bytes(stderr),
        "stderr_size": len(stderr),
    }


def _blocked_command_result(
    *,
    name: str,
    argv: list[str],
    reason: bytes,
    returncode: int,
) -> dict[str, Any]:
    return {
        "name": name,
        "argv": argv,
        "returncode": returncode,
        "passed": False,
        "stdout_sha256": _sha256_bytes(b""),
        "stdout_size": 0,
        "stderr_sha256": _sha256_bytes(reason),
        "stderr_size": len(reason),
    }


def _apply_patch(
    *,
    workspace: Path,
    patch_file: Path,
) -> tuple[bool, bool]:
    check = subprocess.run(
        ["git", "apply", "--check", str(patch_file)],
        cwd=str(workspace),
        capture_output=True,
        check=False,
    )
    if check.returncode != 0:
        return False, False

    apply = subprocess.run(
        ["git", "apply", str(patch_file)],
        cwd=str(workspace),
        capture_output=True,
        check=False,
    )
    return True, apply.returncode == 0


def validate_portable_validation_report(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "research-store portable validation report must be a JSON object"
        )

    expected_keys = set(IDENTITY_FIELDS) | {"report_sha256"}
    if set(report) != expected_keys:
        raise ValueError(
            "research-store portable validation fields do not match reviewed schema"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported research-store portable validation format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected research-store portable validation artifact type"
        )

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError(
            "research-store portable validation source lineage mismatch"
        )

    for field, length in (
        ("reviewed_source_head", 40),
        ("patch_report_sha256", 64),
        ("patch_sha256", 64),
        ("candidate_git_blob", 40),
        ("candidate_sha256", 64),
        ("report_sha256", 64),
    ):
        if not _is_hex_digest(report.get(field), length):
            raise ValueError(
                f"research-store portable validation {field} is invalid"
            )

    if report.get("candidate_git_blob") != EXPECTED_CANDIDATE_BLOB:
        raise ValueError(
            "research-store portable validation candidate blob mismatch"
        )

    for field in ("patch_size", "candidate_size"):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(
                f"research-store portable validation {field} is invalid"
            )

    patch_file = report.get("patch_file")
    if not isinstance(patch_file, str) or not patch_file:
        raise ValueError(
            "research-store portable validation patch file is invalid"
        )
    if report.get("workspace_under_var_tmp") is not True:
        raise ValueError(
            "research-store portable validation workspace scope is invalid"
        )
    if report.get("workspace_source_precedence") is not True:
        raise ValueError(
            "research-store portable validation must bind temporary source precedence"
        )

    python_available = report.get("python_available")
    python_executable = report.get("python_executable")
    pytest_available = report.get("pytest_available")
    if not isinstance(python_available, bool):
        raise ValueError(
            "research-store portable validation Python availability is invalid"
        )
    if not isinstance(pytest_available, bool):
        raise ValueError(
            "research-store portable validation pytest availability is invalid"
        )
    if python_available:
        if (
            not isinstance(python_executable, str)
            or not python_executable.startswith("/")
        ):
            raise ValueError(
                "research-store portable validation Python executable is invalid"
            )
    elif python_executable is not None:
        raise ValueError(
            "research-store unavailable Python executable must be null"
        )
    if pytest_available and not python_available:
        raise ValueError(
            "research-store pytest cannot be available without Python"
        )

    blocker = report.get("validation_blocker")
    if blocker not in {
        None,
        "PATCH_APPLY_FAILED",
        "CANDIDATE_RECONSTRUCTION_MISMATCH",
        "PYTHON_NOT_FOUND",
        "PYTEST_NOT_AVAILABLE",
        "COMMAND_FAILED",
    }:
        raise ValueError(
            "research-store portable validation blocker is invalid"
        )

    for field in ("apply_check_passed", "candidate_reconstruction_passed"):
        if not isinstance(report.get(field), bool):
            raise ValueError(
                f"research-store portable validation {field} is invalid"
            )

    commands = report.get("validation_commands")
    if not isinstance(commands, list) or len(commands) != len(COMMANDS):
        raise ValueError(
            "research-store portable validation command set is invalid"
        )

    for (expected_name, expected_args), item in zip(
        COMMANDS,
        commands,
        strict=True,
    ):
        if not isinstance(item, dict):
            raise ValueError(
                "research-store portable validation command result is invalid"
            )
        if set(item) != {
            "name",
            "argv",
            "returncode",
            "passed",
            "stdout_sha256",
            "stdout_size",
            "stderr_sha256",
            "stderr_size",
        }:
            raise ValueError(
                "research-store portable validation command schema mismatch"
            )
        expected_argv = [
            python_executable if python_available else "python",
            *expected_args,
        ]
        if item["name"] != expected_name or item["argv"] != expected_argv:
            raise ValueError(
                "research-store portable validation command identity mismatch"
            )
        if (
            not isinstance(item["returncode"], int)
            or isinstance(item["returncode"], bool)
        ):
            raise ValueError(
                "research-store portable validation command return code is invalid"
            )
        if not isinstance(item["passed"], bool):
            raise ValueError(
                "research-store portable validation command pass flag is invalid"
            )
        if item["passed"] is not (item["returncode"] == 0):
            raise ValueError(
                "research-store portable validation command pass flag mismatch"
            )
        for digest in ("stdout_sha256", "stderr_sha256"):
            if not _is_hex_digest(item[digest], 64):
                raise ValueError(
                    "research-store portable validation output digest is invalid"
                )
        for size_field in ("stdout_size", "stderr_size"):
            value = item[size_field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(
                    "research-store portable validation output size is invalid"
                )

    all_passed = all(item["passed"] for item in commands)
    if report.get("all_commands_passed") is not all_passed:
        raise ValueError(
            "research-store portable validation aggregate pass mismatch"
        )

    expected_ready = bool(
        report["apply_check_passed"]
        and report["candidate_reconstruction_passed"]
        and python_available
        and pytest_available
        and all_passed
    )
    if report.get("validation_ready") is not expected_ready:
        raise ValueError(
            "research-store portable validation ready flag mismatch"
        )

    if expected_ready:
        expected_blocker = None
    elif not report["apply_check_passed"]:
        expected_blocker = "PATCH_APPLY_FAILED"
    elif not report["candidate_reconstruction_passed"]:
        expected_blocker = "CANDIDATE_RECONSTRUCTION_MISMATCH"
    elif not python_available:
        expected_blocker = "PYTHON_NOT_FOUND"
    elif not pytest_available:
        expected_blocker = "PYTEST_NOT_AVAILABLE"
    else:
        expected_blocker = "COMMAND_FAILED"

    if blocker != expected_blocker:
        raise ValueError(
            "research-store portable validation blocker mismatch"
        )

    if report.get("production_file_modified") is not False:
        raise ValueError(
            "research-store portable validation must not modify production"
        )
    if report.get("requires_separate_mutation_authorization") is not True:
        raise ValueError(
            "research-store portable validation must require separate mutation authorization"
        )

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"research-store portable validation requires {field}=false"
            )

    identity = {field: report[field] for field in IDENTITY_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["report_sha256"] != expected_digest:
        raise ValueError(
            "research-store portable validation report digest mismatch"
        )


def validate_portable_patch(
    *,
    source_tree: str | Path,
    patch_report_path: str | Path,
    patch_file: str | Path,
    python_bin: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")
    _verify_reviewed_source(source)

    exporter = _load_module(
        source / EXPORTER_TOOL,
        "manual_market_paper_research_store_portable_validation_exporter",
    )
    patch_report = _load_json(Path(patch_report_path))
    exporter.validate_portable_patch_report(patch_report)

    patch_path = Path(patch_file).expanduser().resolve(strict=True)
    if patch_path.is_symlink() or not patch_path.is_file():
        raise ValueError("research-store portable patch is not a regular file")
    patch = patch_path.read_bytes()
    if _sha256_bytes(patch) != patch_report["patch_sha256"]:
        raise ValueError(
            "research-store portable patch SHA-256 no longer matches sealed report"
        )
    if len(patch) != patch_report["patch_size"]:
        raise ValueError(
            "research-store portable patch size no longer matches sealed report"
        )

    target = source / RESEARCH_STORE_PATH
    if target.is_symlink() or not target.is_file():
        raise ValueError(
            "reviewed source research store is not a regular file"
        )
    if _git_blob_sha(target) != patch_report["reviewed_target_blob"]:
        raise ValueError(
            "reviewed source research-store blob no longer matches patch report"
        )

    source_head = _reviewed_source_head(source)

    with tempfile.TemporaryDirectory(
        prefix="pio-research-store-portable-validation-",
        dir="/var/tmp",
    ) as tmp:
        root = Path(tmp)
        workspace = root / "source"
        shutil.copytree(
            source,
            workspace,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                "__pycache__",
                ".pytest_cache",
                "target",
            ),
        )

        apply_check_passed, apply_passed = _apply_patch(
            workspace=workspace,
            patch_file=patch_path,
        )

        reconstructed = workspace / RESEARCH_STORE_PATH
        candidate_reconstruction_passed = bool(
            apply_passed
            and reconstructed.is_file()
            and not reconstructed.is_symlink()
            and _git_blob_sha(reconstructed)
            == patch_report["candidate_git_blob"]
            and _sha256_bytes(reconstructed.read_bytes())
            == patch_report["candidate_sha256"]
            and reconstructed.stat().st_size
            == patch_report["candidate_size"]
        )

        python_executable = _resolve_python(python_bin)
        python_available = python_executable is not None
        python_dir = workspace / "python-learner"
        env = _validation_env(python_dir)
        if python_available:
            pytest_available = _probe_pytest(
                python_executable=python_executable,
                cwd=python_dir,
                env=env,
            )
        else:
            pytest_available = False

        if (
            candidate_reconstruction_passed
            and python_available
            and pytest_available
        ):
            command_results = [
                _command_result(
                    name=name,
                    command=(python_executable, *args),
                    cwd=python_dir,
                    env=env,
                )
                for name, args in COMMANDS
            ]
        else:
            if not python_available:
                reason = b"PYTHON_NOT_FOUND\n"
                returncode = 127
            elif not pytest_available:
                reason = b"PYTEST_NOT_AVAILABLE\n"
                returncode = 125
            else:
                reason = b"VALIDATION_PREREQUISITE_FAILED\n"
                returncode = 125
            command_results = [
                _blocked_command_result(
                    name=name,
                    argv=[
                        python_executable if python_available else "python",
                        *args,
                    ],
                    reason=reason,
                    returncode=returncode,
                )
                for name, args in COMMANDS
            ]

    all_passed = all(item["passed"] for item in command_results)
    validation_ready = bool(
        apply_check_passed
        and candidate_reconstruction_passed
        and python_available
        and pytest_available
        and all_passed
    )
    if validation_ready:
        blocker = None
    elif not apply_check_passed:
        blocker = "PATCH_APPLY_FAILED"
    elif not candidate_reconstruction_passed:
        blocker = "CANDIDATE_RECONSTRUCTION_MISMATCH"
    elif not python_available:
        blocker = "PYTHON_NOT_FOUND"
    elif not pytest_available:
        blocker = "PYTEST_NOT_AVAILABLE"
    else:
        blocker = "COMMAND_FAILED"

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "reviewed_source_head": source_head,
        "patch_report_sha256": patch_report["report_sha256"],
        "patch_sha256": patch_report["patch_sha256"],
        "patch_size": patch_report["patch_size"],
        "candidate_git_blob": patch_report["candidate_git_blob"],
        "candidate_sha256": patch_report["candidate_sha256"],
        "candidate_size": patch_report["candidate_size"],
        "patch_file": str(patch_path),
        "workspace_under_var_tmp": True,
        "workspace_source_precedence": True,
        "python_executable": python_executable,
        "python_available": python_available,
        "pytest_available": pytest_available,
        "validation_blocker": blocker,
        "apply_check_passed": apply_check_passed,
        "candidate_reconstruction_passed": candidate_reconstruction_passed,
        "validation_commands": command_results,
        "all_commands_passed": all_passed,
        "validation_ready": validation_ready,
        "production_file_modified": False,
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
        "report_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }
    validate_portable_validation_report(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the sealed portable research_store.py patch on a "
            "non-production host. The tool reconstructs the exact candidate "
            "only in a temporary reviewed-source copy under /var/tmp and runs "
            "py_compile plus the full pytest suite with an existing Python "
            "environment. It installs no packages."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--patch-report", required=True)
    parser.add_argument("--patch-file", required=True)
    parser.add_argument(
        "--python-bin",
        help=(
            "Optional existing Python executable path/name; no package or "
            "environment installation is performed."
        ),
    )
    args = parser.parse_args()

    report = validate_portable_patch(
        source_tree=args.source_tree,
        patch_report_path=args.patch_report,
        patch_file=args.patch_file,
        python_bin=args.python_bin,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["validation_ready"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
