#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path
import shlex
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
RUNNER_TOOL = TOOLS_DIR / "run_phase2_isolated_next_preflight.py"
_ALLOWED_MUTATION_FLAGS = frozenset({"--apply", "--prepare"})


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


def render_reviewed_mutation_command(
    *,
    timeout_seconds: int = 60,
    **handoff_kwargs: Any,
) -> Phase2ReviewedMutationCommand:
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

    return Phase2ReviewedMutationCommand(
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
        preflight=preflight.to_record(),
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
