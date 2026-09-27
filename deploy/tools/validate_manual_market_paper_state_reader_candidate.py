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


FORMAT_VERSION = 2
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_STATE_READER_CANDIDATE_VALIDATION_V2"

BUILDER_TOOL = Path("deploy/tools/build_manual_market_paper_state_reader_candidate.py")
REVIEWED_SOURCE_BLOBS = {
    BUILDER_TOOL: "1c97fc8db482de015b2463f6d2a4823e8003628e",
}

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
    "candidate_report_sha256",
    "candidate_git_blob",
    "candidate_sha256",
    "candidate_size",
    "candidate_path",
    "validation_workspace_under_var_tmp",
    "cargo_executable",
    "cargo_available",
    "validation_blocker",
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
            raise ValueError(f"reviewed validation artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed validation artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed validation artifact mismatch: {relative}")


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
        if (
            resolved_path.is_file()
            and os.access(resolved_path, os.X_OK)
        ):
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


def _missing_cargo_result(
    *,
    name: str,
    command_args: tuple[str, ...],
) -> dict[str, Any]:
    stdout = b""
    stderr = b"CARGO_NOT_FOUND\n"
    return {
        "name": name,
        "argv": ["cargo", *command_args],
        "returncode": 127,
        "passed": False,
        "stdout_sha256": _sha256_bytes(stdout),
        "stdout_size": 0,
        "stderr_sha256": _sha256_bytes(stderr),
        "stderr_size": len(stderr),
    }


def validate_validation_report(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("candidate validation report must be a JSON object")

    expected_keys = set(IDENTITY_FIELDS) | {"report_sha256"}
    if set(report) != expected_keys:
        raise ValueError("candidate validation report fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported candidate validation report format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected candidate validation artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("candidate validation source lineage mismatch")

    for field, length in (
        ("reviewed_source_head", 40),
        ("candidate_report_sha256", 64),
        ("candidate_git_blob", 40),
        ("candidate_sha256", 64),
        ("report_sha256", 64),
    ):
        if not _is_hex_digest(report.get(field), length):
            raise ValueError(f"candidate validation {field} is invalid")

    size = report.get("candidate_size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("candidate validation size is invalid")

    candidate_path = report.get("candidate_path")
    if (
        not isinstance(candidate_path, str)
        or not candidate_path.startswith("/var/tmp/")
    ):
        raise ValueError("candidate validation path must remain under /var/tmp")
    if report.get("validation_workspace_under_var_tmp") is not True:
        raise ValueError("candidate validation workspace scope is invalid")

    cargo_executable = report.get("cargo_executable")
    cargo_available = report.get("cargo_available")
    if not isinstance(cargo_available, bool):
        raise ValueError("candidate validation cargo availability is invalid")
    if cargo_available:
        if (
            not isinstance(cargo_executable, str)
            or not cargo_executable.startswith("/")
        ):
            raise ValueError("candidate validation cargo executable is invalid")
    elif cargo_executable is not None:
        raise ValueError(
            "candidate validation unavailable cargo executable must be null"
        )

    blocker = report.get("validation_blocker")
    if blocker not in {None, "CARGO_NOT_FOUND", "COMMAND_FAILED"}:
        raise ValueError("candidate validation blocker is invalid")

    commands = report.get("validation_commands")
    if not isinstance(commands, list) or len(commands) != len(COMMANDS):
        raise ValueError("candidate validation command set is invalid")

    expected_names = [item[0] for item in COMMANDS]
    if [item.get("name") for item in commands] != expected_names:
        raise ValueError("candidate validation command ordering is invalid")

    for (expected_name, expected_args), item in zip(COMMANDS, commands, strict=True):
        if not isinstance(item, dict):
            raise ValueError("candidate validation command result is invalid")
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
            raise ValueError("candidate validation command schema mismatch")
        expected_argv = [
            cargo_executable if cargo_available else "cargo",
            *expected_args,
        ]
        if item["name"] != expected_name or item["argv"] != expected_argv:
            raise ValueError("candidate validation command identity mismatch")
        if (
            not isinstance(item["returncode"], int)
            or isinstance(item["returncode"], bool)
        ):
            raise ValueError("candidate validation return code is invalid")
        if not isinstance(item["passed"], bool):
            raise ValueError("candidate validation passed flag is invalid")
        if item["passed"] is not (item["returncode"] == 0):
            raise ValueError("candidate validation passed flag mismatch")
        for digest in ("stdout_sha256", "stderr_sha256"):
            if not _is_hex_digest(item[digest], 64):
                raise ValueError("candidate validation output digest is invalid")
        for size_field in ("stdout_size", "stderr_size"):
            if (
                not isinstance(item[size_field], int)
                or isinstance(item[size_field], bool)
                or item[size_field] < 0
            ):
                raise ValueError("candidate validation output size is invalid")

    all_passed = all(item["passed"] for item in commands)
    if report.get("all_commands_passed") is not all_passed:
        raise ValueError("candidate validation aggregate pass flag mismatch")
    expected_ready = cargo_available and all_passed
    if report.get("validation_ready") is not expected_ready:
        raise ValueError("candidate validation ready flag mismatch")

    expected_blocker = (
        None
        if expected_ready
        else ("CARGO_NOT_FOUND" if not cargo_available else "COMMAND_FAILED")
    )
    if blocker != expected_blocker:
        raise ValueError("candidate validation blocker mismatch")
    if report.get("production_file_modified") is not False:
        raise ValueError("candidate validation must not modify production")
    if report.get("requires_separate_mutation_authorization") is not True:
        raise ValueError("candidate validation must require separate mutation authorization")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if report.get(field) is not False:
            raise ValueError(f"candidate validation requires {field}=false")

    identity = {field: report[field] for field in IDENTITY_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["report_sha256"] != expected_digest:
        raise ValueError("candidate validation report digest mismatch")


def validate_candidate(
    *,
    source_tree: str | Path,
    candidate_report: dict[str, Any],
    cargo_bin: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")

    _verify_reviewed_source(source)
    builder_module = _load_module(
        source / BUILDER_TOOL,
        "manual_market_paper_state_reader_candidate_validation_builder",
    )
    builder_module.validate_candidate_report(candidate_report)

    candidate_path = Path(str(candidate_report["output_path"]))
    candidate_path_resolved = candidate_path.resolve(strict=True)
    var_tmp = Path("/var/tmp").resolve()
    if var_tmp not in candidate_path_resolved.parents:
        raise ValueError("candidate file escaped /var/tmp")
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise ValueError("candidate file is not a regular file")

    candidate = candidate_path.read_bytes()
    if _git_blob_sha_bytes(candidate) != candidate_report["candidate_git_blob"]:
        raise ValueError("candidate Git blob no longer matches sealed report")
    if _sha256_bytes(candidate) != candidate_report["candidate_sha256"]:
        raise ValueError("candidate SHA-256 no longer matches sealed report")
    if len(candidate) != candidate_report["candidate_size"]:
        raise ValueError("candidate size no longer matches sealed report")

    source_head = _reviewed_source_head(source)

    with tempfile.TemporaryDirectory(
        prefix="pio-state-reader-validation-",
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

        target_path = workspace / str(candidate_report["path"])
        if not target_path.is_file() or target_path.is_symlink():
            raise ValueError("temporary validation target is not a regular file")
        target_path.write_bytes(candidate)

        if _git_blob_sha(target_path) != candidate_report["candidate_git_blob"]:
            raise ValueError("temporary candidate install verification failed")

        rust_dir = workspace / "rust-executor"
        env = dict(os.environ)
        env["CARGO_BUILD_JOBS"] = "1"
        env["CARGO_TARGET_DIR"] = str(cargo_target)

        cargo_executable = _resolve_cargo(cargo_bin)
        cargo_available = cargo_executable is not None
        if cargo_available:
            command_results = [
                _command_result(
                    name=name,
                    command=(cargo_executable, *command_args),
                    cwd=rust_dir,
                    env=env,
                )
                for name, command_args in COMMANDS
            ]
        else:
            command_results = [
                _missing_cargo_result(
                    name=name,
                    command_args=command_args,
                )
                for name, command_args in COMMANDS
            ]

    all_passed = all(item["passed"] for item in command_results)
    validation_ready = cargo_available and all_passed
    validation_blocker = (
        None
        if validation_ready
        else ("CARGO_NOT_FOUND" if not cargo_available else "COMMAND_FAILED")
    )
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
        "candidate_report_sha256": candidate_report["report_sha256"],
        "candidate_git_blob": candidate_report["candidate_git_blob"],
        "candidate_sha256": candidate_report["candidate_sha256"],
        "candidate_size": candidate_report["candidate_size"],
        "candidate_path": str(candidate_path_resolved),
        "validation_workspace_under_var_tmp": True,
        "cargo_executable": cargo_executable,
        "cargo_available": cargo_available,
        "validation_blocker": validation_blocker,
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
    validate_validation_report(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a sealed state-reader preserved-fix candidate in a "
            "temporary copy of the reviewed source. The production repository "
            "is never read or written by this tool. Rust tests run with "
            "CARGO_BUILD_JOBS=1 and a temporary CARGO_TARGET_DIR."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--candidate-report", required=True)
    parser.add_argument(
        "--cargo-bin",
        help=(
            "Optional existing Cargo executable path/name. No toolchain is "
            "installed or modified by this validator."
        ),
    )
    args = parser.parse_args()

    report = validate_candidate(
        source_tree=args.source_tree,
        candidate_report=_load_json(Path(args.candidate_report)),
        cargo_bin=args.cargo_bin,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["validation_ready"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
