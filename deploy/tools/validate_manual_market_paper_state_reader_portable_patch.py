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
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_STATE_READER_PORTABLE_VALIDATION_V1"

EXPORTER_TOOL = Path("deploy/tools/export_manual_market_paper_state_reader_candidate_patch.py")
STATE_READER_PATH = Path("rust-executor/src/state_reader.rs")

REVIEWED_SOURCE_BLOBS = {
    EXPORTER_TOOL: "c3d18d14b0b8ba983c9b26f8d3f6ade7da88d852",
}

EXPECTED_CANDIDATE_BLOB = "f54a1021cf8f89d285bde957d1f72d81857ec2fa"

COMMANDS = (
    ("cargo_test", ("test", "--quiet")),
    (
        "cargo_test_live_submit",
        ("test", "--quiet", "--features", "live-submit"),
    ),
)

CARGO_COMMON_PATHS = (
    Path.home() / ".cargo" / "bin" / "cargo",
    Path("/usr/bin/cargo"),
    Path("/usr/local/bin/cargo"),
    Path("/usr/local/cargo/bin/cargo"),
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
    "cargo_executable",
    "cargo_available",
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
            raise ValueError(f"reviewed portable-validation artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed portable-validation artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed portable-validation artifact mismatch: {relative}")


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


def _resolve_cargo(cargo_bin: str | None) -> str | None:
    candidates: list[Path] = []
    if cargo_bin:
        raw = Path(cargo_bin).expanduser()
        if raw.is_absolute() or "/" in cargo_bin:
            candidates.append(raw)
        else:
            resolved = shutil.which(cargo_bin)
            if resolved:
                candidates.append(Path(resolved))
    else:
        resolved = shutil.which("cargo")
        if resolved:
            candidates.append(Path(resolved))
        candidates.extend(CARGO_COMMON_PATHS)

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
        stdout = proc.stdout if isinstance(proc.stdout, bytes) else str(proc.stdout).encode()
        stderr = proc.stderr if isinstance(proc.stderr, bytes) else str(proc.stderr).encode()
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
        raise ValueError("portable validation report must be a JSON object")

    expected_keys = set(IDENTITY_FIELDS) | {"report_sha256"}
    if set(report) != expected_keys:
        raise ValueError("portable validation fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported portable validation format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected portable validation artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("portable validation source lineage mismatch")

    for field, length in (
        ("reviewed_source_head", 40),
        ("patch_report_sha256", 64),
        ("patch_sha256", 64),
        ("candidate_git_blob", 40),
        ("candidate_sha256", 64),
        ("report_sha256", 64),
    ):
        if not _is_hex_digest(report.get(field), length):
            raise ValueError(f"portable validation {field} is invalid")

    if report.get("candidate_git_blob") != EXPECTED_CANDIDATE_BLOB:
        raise ValueError("portable validation candidate blob mismatch")

    for field in ("patch_size", "candidate_size"):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"portable validation {field} is invalid")

    patch_file = report.get("patch_file")
    if not isinstance(patch_file, str) or not patch_file:
        raise ValueError("portable validation patch file is invalid")
    if report.get("workspace_under_var_tmp") is not True:
        raise ValueError("portable validation workspace scope is invalid")

    cargo_available = report.get("cargo_available")
    cargo_executable = report.get("cargo_executable")
    if not isinstance(cargo_available, bool):
        raise ValueError("portable validation cargo availability is invalid")
    if cargo_available:
        if not isinstance(cargo_executable, str) or not cargo_executable.startswith("/"):
            raise ValueError("portable validation cargo executable is invalid")
    elif cargo_executable is not None:
        raise ValueError("portable validation unavailable cargo executable must be null")

    blocker = report.get("validation_blocker")
    if blocker not in {
        None,
        "PATCH_APPLY_FAILED",
        "CANDIDATE_RECONSTRUCTION_MISMATCH",
        "CARGO_NOT_FOUND",
        "COMMAND_FAILED",
    }:
        raise ValueError("portable validation blocker is invalid")

    for field in ("apply_check_passed", "candidate_reconstruction_passed"):
        if not isinstance(report.get(field), bool):
            raise ValueError(f"portable validation {field} is invalid")

    commands = report.get("validation_commands")
    if not isinstance(commands, list) or len(commands) != len(COMMANDS):
        raise ValueError("portable validation command set is invalid")
    for (expected_name, expected_args), item in zip(COMMANDS, commands, strict=True):
        if not isinstance(item, dict):
            raise ValueError("portable validation command result is invalid")
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
            raise ValueError("portable validation command schema mismatch")
        expected_argv = [
            cargo_executable if cargo_available else "cargo",
            *expected_args,
        ]
        if item["name"] != expected_name or item["argv"] != expected_argv:
            raise ValueError("portable validation command identity mismatch")
        if not isinstance(item["returncode"], int) or isinstance(item["returncode"], bool):
            raise ValueError("portable validation command return code is invalid")
        if not isinstance(item["passed"], bool):
            raise ValueError("portable validation command pass flag is invalid")
        if item["passed"] is not (item["returncode"] == 0):
            raise ValueError("portable validation command pass flag mismatch")
        for digest in ("stdout_sha256", "stderr_sha256"):
            if not _is_hex_digest(item[digest], 64):
                raise ValueError("portable validation output digest is invalid")
        for size_field in ("stdout_size", "stderr_size"):
            value = item[size_field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("portable validation output size is invalid")

    all_passed = all(item["passed"] for item in commands)
    if report.get("all_commands_passed") is not all_passed:
        raise ValueError("portable validation aggregate pass mismatch")

    expected_ready = bool(
        report["apply_check_passed"]
        and report["candidate_reconstruction_passed"]
        and cargo_available
        and all_passed
    )
    if report.get("validation_ready") is not expected_ready:
        raise ValueError("portable validation ready flag mismatch")

    if expected_ready:
        expected_blocker = None
    elif not report["apply_check_passed"]:
        expected_blocker = "PATCH_APPLY_FAILED"
    elif not report["candidate_reconstruction_passed"]:
        expected_blocker = "CANDIDATE_RECONSTRUCTION_MISMATCH"
    elif not cargo_available:
        expected_blocker = "CARGO_NOT_FOUND"
    else:
        expected_blocker = "COMMAND_FAILED"
    if blocker != expected_blocker:
        raise ValueError("portable validation blocker mismatch")

    if report.get("production_file_modified") is not False:
        raise ValueError("portable validation must not modify production")
    if report.get("requires_separate_mutation_authorization") is not True:
        raise ValueError("portable validation must require separate mutation authorization")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if report.get(field) is not False:
            raise ValueError(f"portable validation requires {field}=false")

    identity = {field: report[field] for field in IDENTITY_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["report_sha256"] != expected_digest:
        raise ValueError("portable validation report digest mismatch")


def validate_portable_patch(
    *,
    source_tree: str | Path,
    patch_report_path: str | Path,
    patch_file: str | Path,
    cargo_bin: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")
    _verify_reviewed_source(source)

    exporter = _load_module(
        source / EXPORTER_TOOL,
        "manual_market_paper_portable_validation_exporter",
    )
    patch_report = _load_json(Path(patch_report_path))
    exporter.validate_portable_patch_report(patch_report)

    patch_path = Path(patch_file).expanduser().resolve(strict=True)
    if patch_path.is_symlink() or not patch_path.is_file():
        raise ValueError("portable patch is not a regular file")
    patch = patch_path.read_bytes()
    if _sha256_bytes(patch) != patch_report["patch_sha256"]:
        raise ValueError("portable patch SHA-256 no longer matches sealed report")
    if len(patch) != patch_report["patch_size"]:
        raise ValueError("portable patch size no longer matches sealed report")

    target = source / STATE_READER_PATH
    if target.is_symlink() or not target.is_file():
        raise ValueError("reviewed source state reader is not a regular file")
    if _git_blob_sha(target) != patch_report["reviewed_target_blob"]:
        raise ValueError("reviewed source target blob no longer matches patch report")

    source_head = _reviewed_source_head(source)

    with tempfile.TemporaryDirectory(
        prefix="pio-portable-candidate-validation-",
        dir="/var/tmp",
    ) as tmp:
        root = Path(tmp)
        workspace = root / "source"
        cargo_target = root / "cargo-target"
        shutil.copytree(
            source,
            workspace,
            ignore=shutil.ignore_patterns(
                ".git",
                "target",
                ".venv",
                "__pycache__",
                ".pytest_cache",
            ),
        )

        apply_check_passed, apply_passed = _apply_patch(
            workspace=workspace,
            patch_file=patch_path,
        )

        reconstructed = workspace / STATE_READER_PATH
        candidate_reconstruction_passed = bool(
            apply_passed
            and reconstructed.is_file()
            and not reconstructed.is_symlink()
            and _git_blob_sha(reconstructed) == patch_report["candidate_git_blob"]
            and _sha256_bytes(reconstructed.read_bytes()) == patch_report["candidate_sha256"]
            and reconstructed.stat().st_size == patch_report["candidate_size"]
        )

        cargo_executable = _resolve_cargo(cargo_bin)
        cargo_available = cargo_executable is not None

        rust_dir = workspace / "rust-executor"
        env = dict(os.environ)
        env["CARGO_BUILD_JOBS"] = "1"
        env["CARGO_TARGET_DIR"] = str(cargo_target)

        if candidate_reconstruction_passed and cargo_available:
            command_results = [
                _command_result(
                    name=name,
                    command=(cargo_executable, *args),
                    cwd=rust_dir,
                    env=env,
                )
                for name, args in COMMANDS
            ]
        else:
            command_results = [
                {
                    "name": name,
                    "argv": [
                        cargo_executable if cargo_available else "cargo",
                        *args,
                    ],
                    "returncode": 125 if cargo_available else 127,
                    "passed": False,
                    "stdout_sha256": _sha256_bytes(b""),
                    "stdout_size": 0,
                    "stderr_sha256": _sha256_bytes(
                        (
                            b"VALIDATION_PREREQUISITE_FAILED\n"
                            if cargo_available
                            else b"CARGO_NOT_FOUND\n"
                        )
                    ),
                    "stderr_size": (
                        len(b"VALIDATION_PREREQUISITE_FAILED\n")
                        if cargo_available
                        else len(b"CARGO_NOT_FOUND\n")
                    ),
                }
                for name, args in COMMANDS
            ]

    all_passed = all(item["passed"] for item in command_results)
    validation_ready = bool(
        apply_check_passed
        and candidate_reconstruction_passed
        and cargo_available
        and all_passed
    )
    if validation_ready:
        blocker = None
    elif not apply_check_passed:
        blocker = "PATCH_APPLY_FAILED"
    elif not candidate_reconstruction_passed:
        blocker = "CANDIDATE_RECONSTRUCTION_MISMATCH"
    elif not cargo_available:
        blocker = "CARGO_NOT_FOUND"
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
        "cargo_executable": cargo_executable,
        "cargo_available": cargo_available,
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
            "Validate the sealed portable state-reader patch on a non-production "
            "host. The tool reconstructs the exact candidate only in a temporary "
            "reviewed-source copy under /var/tmp and runs default + live-submit "
            "Rust tests when an existing Cargo executable is available."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--patch-report", required=True)
    parser.add_argument("--patch-file", required=True)
    parser.add_argument(
        "--cargo-bin",
        help="Optional existing Cargo executable path/name; no toolchain is installed.",
    )
    args = parser.parse_args()

    report = validate_portable_patch(
        source_tree=args.source_tree,
        patch_report_path=args.patch_report,
        patch_file=args.patch_file,
        cargo_bin=args.cargo_bin,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["validation_ready"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
