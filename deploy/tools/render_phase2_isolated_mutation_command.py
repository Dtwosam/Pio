#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
RUNNER_TOOL = TOOLS_DIR / "run_phase2_isolated_next_preflight.py"
_ALLOWED_MUTATION_FLAGS = frozenset({"--apply", "--prepare"})
MUTATION_PREVIEW_FORMAT_VERSION = 2
MUTATION_FINGERPRINT_SCHEMA = "PHASE2_MUTATION_PREVIEW_V2"
_DEPLOY_SURFACE_DOMAIN = b"PIO_DEPLOY_SURFACE_V1\0"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = _load(RUNNER_TOOL, "phase2_reviewed_mutation_preflight")


@dataclass(frozen=True)
class Phase2ReviewedMutationCommand:
    format_version: int
    fingerprint_schema: str
    reviewed_source_commit: str
    deploy_surface_sha256: str
    deploy_surface_files: int
    state: str
    next_action: str
    next_tool: str | None
    preflight_executed: bool
    preflight_exit_code: int | None
    preflight_json_valid: bool
    preflight_succeeded: bool
    mutation_flag: str | None
    mutation_argv: tuple[str, ...] | None
    mutation_command: str | None
    mutation_rendered: bool
    mutation_executed: bool
    mutation_tool_sha256: str | None
    preflight_fingerprint: str
    mutation_fingerprint: str | None
    preflight: dict[str, Any]
    lifecycle: dict[str, Any]
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    daemon_reload_performed: bool
    production_tree_modified: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _preflight_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "daemon_reload_performed", True)
        and not getattr(report, "production_tree_modified", True)
        and not getattr(report, "mutation_flag_appended", True)
    )


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        check=False,
    )


def _deploy_surface_identity(
    repo_root: Path | None = None,
) -> tuple[str, str, int]:
    root = (repo_root or REPO_ROOT).resolve()
    top = _run_git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root:
        raise ValueError("reviewed deploy surface is not inside expected Git root")

    head = _run_git(root, "rev-parse", "HEAD")
    source_commit = head.stdout.strip() if head.returncode == 0 else ""
    if not source_commit or any(
        value not in "0123456789abcdef" for value in source_commit
    ):
        raise ValueError("reviewed deploy surface HEAD is invalid")

    dirty = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
        "--",
        "deploy",
    )
    if dirty.returncode != 0:
        raise ValueError("cannot inspect reviewed deploy surface status")
    if dirty.stdout.strip():
        raise ValueError("reviewed deploy surface has tracked local changes")

    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "deploy"],
        cwd=str(root),
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        raise ValueError("cannot enumerate reviewed deploy surface")
    relative_paths = sorted(
        value.decode("utf-8")
        for value in listed.stdout.split(b"\0")
        if value
    )
    if not relative_paths:
        raise ValueError("reviewed deploy surface is empty")

    digest = hashlib.sha256()
    digest.update(_DEPLOY_SURFACE_DOMAIN)
    count = 0
    for relative in relative_paths:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"reviewed deploy surface contains non-regular file: {relative}"
            )
        payload = path.read_bytes()
        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        count += 1
    return source_commit, digest.hexdigest(), count


def _fingerprint(kind: str, value: Any) -> str:
    if not kind:
        raise ValueError("fingerprint kind is required")
    encoded = json.dumps(
        {
            "schema": MUTATION_FINGERPRINT_SCHEMA,
            "kind": kind,
            "value": value,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mutation_tool_sha256(
    mutation_argv: tuple[str, ...] | None,
) -> str | None:
    if mutation_argv is None:
        return None
    if len(mutation_argv) < 2:
        raise ValueError("mutation argv is missing reviewed tool")
    tool = Path(mutation_argv[1])
    if tool.is_symlink() or not tool.is_file():
        raise ValueError("mutation tool is missing or symlinked")
    resolved = tool.resolve()
    try:
        resolved.relative_to(TOOLS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("mutation tool resolves outside reviewed tools") from exc
    if resolved.name not in RUNNER.REVIEWED_PREFLIGHT_TOOLS:
        raise ValueError("mutation tool is not allowlisted")
    return hashlib.sha256(resolved.read_bytes()).hexdigest()


def render_reviewed_mutation_command(
    *,
    timeout_seconds: int = 60,
    **handoff_kwargs: Any,
) -> Phase2ReviewedMutationCommand:
    (
        reviewed_source_commit,
        deploy_surface_sha256,
        deploy_surface_files,
    ) = _deploy_surface_identity()

    preflight = RUNNER.run_next_read_only_preflight(
        timeout_seconds=timeout_seconds,
        **handoff_kwargs,
    )
    if not _preflight_boundary_ok(preflight):
        raise ValueError("Phase-2 mutation preview crossed the read-only boundary")

    mutation_flag = getattr(preflight, "mutation_flag", None)
    if mutation_flag is not None and mutation_flag not in _ALLOWED_MUTATION_FLAGS:
        raise ValueError("unexpected lifecycle mutation flag")

    preflight_succeeded = bool(
        preflight.command_executed
        and preflight.result_json_valid
        and preflight.exit_code == 0
        and preflight.failure_category is None
    )

    mutation_argv: tuple[str, ...] | None = None
    mutation_command: str | None = None
    if mutation_flag is not None and preflight_succeeded:
        raw = getattr(preflight, "preflight_argv", None)
        if not isinstance(raw, (tuple, list)) or len(raw) < 2:
            raise ValueError("successful preflight is missing reviewed argv")
        argv = tuple(str(value) for value in raw)
        if any(value in _ALLOWED_MUTATION_FLAGS for value in argv[2:]):
            raise ValueError("preflight argv already contains a mutation flag")
        mutation_argv = (*argv, mutation_flag)
        mutation_command = shlex.join(mutation_argv)

    mutation_tool_sha256 = _mutation_tool_sha256(mutation_argv)
    preflight_record = preflight.to_record()
    preflight_fingerprint = _fingerprint(
        "preflight",
        {
            "reviewed_source_commit": reviewed_source_commit,
            "deploy_surface_sha256": deploy_surface_sha256,
            "deploy_surface_files": deploy_surface_files,
            "preflight": preflight_record,
        },
    )
    mutation_fingerprint = (
        _fingerprint(
            "mutation",
            {
                "reviewed_source_commit": reviewed_source_commit,
                "deploy_surface_sha256": deploy_surface_sha256,
                "deploy_surface_files": deploy_surface_files,
                "preflight_fingerprint": preflight_fingerprint,
                "mutation_argv": mutation_argv,
                "mutation_tool_sha256": mutation_tool_sha256,
            },
        )
        if mutation_argv is not None
        else None
    )

    return Phase2ReviewedMutationCommand(
        format_version=MUTATION_PREVIEW_FORMAT_VERSION,
        fingerprint_schema=MUTATION_FINGERPRINT_SCHEMA,
        reviewed_source_commit=reviewed_source_commit,
        deploy_surface_sha256=deploy_surface_sha256,
        deploy_surface_files=deploy_surface_files,
        state=str(preflight.state),
        next_action=str(preflight.next_action),
        next_tool=preflight.next_tool,
        preflight_executed=bool(preflight.command_executed),
        preflight_exit_code=preflight.exit_code,
        preflight_json_valid=bool(preflight.result_json_valid),
        preflight_succeeded=preflight_succeeded,
        mutation_flag=mutation_flag,
        mutation_argv=mutation_argv,
        mutation_command=mutation_command,
        mutation_rendered=mutation_argv is not None,
        mutation_executed=False,
        mutation_tool_sha256=mutation_tool_sha256,
        preflight_fingerprint=preflight_fingerprint,
        mutation_fingerprint=mutation_fingerprint,
        preflight=preflight_record,
        lifecycle=preflight.lifecycle,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        daemon_reload_performed=False,
        production_tree_modified=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the reviewed read-only Phase-2 next-step preflight and, "
            "only when it succeeds, render the exact reviewed mutation "
            "command without executing it."
        )
    )
    parser.add_argument("--runtime-root", default="/opt/pio-phase2-runtime")
    parser.add_argument("--unit-destination", default="/etc/systemd/system")
    parser.add_argument("--env-file", default="/etc/pio/pio.env")
    parser.add_argument("--data-root", default="/opt/pio/data")
    parser.add_argument(
        "--receipt-path",
        default="/opt/pio/data/phase2-isolated-smoke-receipt.json",
    )
    parser.add_argument("--max-receipt-age-seconds", type=int, default=1800)
    parser.add_argument("--source-tree")
    parser.add_argument(
        "--repository-url",
        default=RUNNER.RENDER.HANDOFF.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()

    report = render_reviewed_mutation_command(
        timeout_seconds=args.timeout_seconds,
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        receipt_path=args.receipt_path,
        max_receipt_age_seconds=args.max_receipt_age_seconds,
        source_tree=args.source_tree,
        repository_url=args.repository_url,
    )
    print(json.dumps(report.to_record(), indent=2))


if __name__ == "__main__":
    main()
