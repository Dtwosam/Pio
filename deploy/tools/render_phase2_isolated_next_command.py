#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path
import re
import shlex
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
HANDOFF_TOOL = TOOLS_DIR / "check_phase2_isolated_lifecycle_handoff.py"
_SAFE_PARAMETER = re.compile(r"^[a-z][a-z0-9_]*$")


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HANDOFF = _load(HANDOFF_TOOL, "phase2_lifecycle_command_handoff")


@dataclass(frozen=True)
class Phase2LifecycleCommandPreview:
    state: str
    next_action: str
    next_tool: str | None
    preflight_argv: tuple[str, ...] | None
    preflight_command: str | None
    mutation_flag: str | None
    mutation_flag_appended: bool
    lifecycle: dict[str, Any]
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
    )


def _reviewed_tool(name: str | None) -> Path | None:
    if name is None:
        return None
    if not name or Path(name).name != name:
        raise ValueError("next tool name is not a reviewed local basename")
    candidate = TOOLS_DIR / name
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"reviewed next tool is missing: {name}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(TOOLS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("next tool resolves outside reviewed tools directory") from exc
    return resolved


def _parameter_args(parameters: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for key, value in parameters.items():
        if not isinstance(key, str) or not _SAFE_PARAMETER.fullmatch(key):
            raise ValueError(f"unsafe next-step parameter name: {key!r}")
        if isinstance(value, bool) or value is None:
            raise ValueError(
                f"unsupported next-step parameter value for {key}"
            )
        if not isinstance(value, (str, int, float)):
            raise ValueError(
                f"unsupported next-step parameter type for {key}"
            )
        rendered = str(value)
        if "\x00" in rendered:
            raise ValueError(
                f"next-step parameter contains NUL: {key}"
            )
        args.extend(("--" + key.replace("_", "-"), rendered))
    return args


def render_lifecycle_command(
    *,
    python_executable: str = "python3",
    **handoff_kwargs: Any,
) -> Phase2LifecycleCommandPreview:
    if not python_executable.strip() or "\x00" in python_executable:
        raise ValueError("python_executable is invalid")

    handoff = HANDOFF.inspect_lifecycle_handoff(**handoff_kwargs)
    if not _boundary_ok(handoff):
        raise ValueError("Phase-2 lifecycle command crossed the read-only boundary")

    tool = _reviewed_tool(handoff.next_tool)
    argv: tuple[str, ...] | None = None
    command: str | None = None

    if tool is not None:
        parameters = getattr(handoff, "next_parameters", None)
        if not isinstance(parameters, dict):
            raise ValueError("lifecycle handoff is missing next_parameters")
        raw = [
            python_executable,
            str(tool),
            *_parameter_args(parameters),
        ]
        # Intentionally do not append handoff.next_mutation_flag here.
        argv = tuple(raw)
        command = shlex.join(raw)

    mutation_flag = getattr(handoff, "next_mutation_flag", None)
    if mutation_flag is not None:
        if mutation_flag not in {"--apply", "--prepare"}:
            raise ValueError("unexpected lifecycle mutation flag")

    return Phase2LifecycleCommandPreview(
        state=str(handoff.state),
        next_action=str(handoff.next_action),
        next_tool=handoff.next_tool,
        preflight_argv=argv,
        preflight_command=command,
        mutation_flag=mutation_flag,
        mutation_flag_appended=False,
        lifecycle=handoff.to_record(),
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Render the reviewed read-only preflight command for the next "
            "isolated Phase-2 lifecycle step. Mutation flags are reported "
            "separately and never appended or executed."
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
        default=HANDOFF.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    )
    parser.add_argument("--python", default="python3")
    args = parser.parse_args()

    report = render_lifecycle_command(
        python_executable=args.python,
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
