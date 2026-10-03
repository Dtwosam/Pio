#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
from pathlib import Path
import re
import stat
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
LIFECYCLE_TOOL = TOOLS_DIR / "check_phase2_isolated_lifecycle_handoff.py"
SAVE_TOOL = TOOLS_DIR / "save_phase2_isolated_mutation_preview.py"
FRESH_TOOL = TOOLS_DIR / "check_phase2_isolated_mutation_freshness.py"
AUDIT_TOOL = TOOLS_DIR / "check_phase2_isolated_mutation_execution_receipt.py"

_PROTECTED_ARTIFACT_ROOTS = (
    Path("/opt/pio"),
    Path("/opt/pio/data"),
    Path("/opt/pio-phase2-runtime"),
    Path("/etc/pio"),
    Path("/etc/systemd/system"),
)
_STATE_SLUG = re.compile(r"[^a-z0-9]+")


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LIFECYCLE = _load(LIFECYCLE_TOOL, "phase2_artifact_handoff_lifecycle")
SAVE = _load(SAVE_TOOL, "phase2_artifact_handoff_save")
FRESH = _load(FRESH_TOOL, "phase2_artifact_handoff_freshness")
AUDIT = _load(AUDIT_TOOL, "phase2_artifact_handoff_audit")


@dataclass(frozen=True)
class Phase2ArtifactAwareHandoff:
    state: str
    next_action: str
    next_tool: str | None
    next_parameters: dict[str, Any]
    next_mutation_flag: str | None
    lifecycle_state: str
    lifecycle_next_action: str
    lifecycle_next_tool: str | None
    lifecycle_mutation_flag: str | None
    preview_path: str | None
    preview_sha256: str | None
    preview_current: bool | None
    execution_receipt_path: str | None
    receipt_status: str | None
    receipt_post_mutation_verified: bool | None
    attention_required: bool
    blockers: tuple[str, ...]
    lifecycle: dict[str, Any]
    freshness: dict[str, Any] | None
    audit: dict[str, Any] | None
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    artifact_write_performed: bool
    service_control_performed: bool
    mutation_executed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _artifact_root(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("artifact root must not be a symlink")
    root = raw.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("artifact root must be a directory")
    if any(_under(root, item.resolve()) for item in _PROTECTED_ARTIFACT_ROOTS):
        raise ValueError("artifact root is inside a protected production path")
    return root


def _lifecycle_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
    )


def _safe_repository_url(value: str) -> str:
    _, safe = LIFECYCLE.BOOTSTRAP._validated_repository_url(value)
    return safe


def _common_parameters(
    *,
    runtime_root: str | Path,
    unit_destination: str | Path,
    env_file: str | Path,
    data_root: str | Path,
    receipt_path: str | Path,
    max_receipt_age_seconds: int,
    source_tree: str | Path | None,
    repository_url: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "runtime_root": str(Path(runtime_root).expanduser()),
        "unit_destination": str(Path(unit_destination).expanduser()),
        "env_file": str(Path(env_file).expanduser()),
        "data_root": str(Path(data_root).expanduser()),
        "receipt_path": str(Path(receipt_path).expanduser()),
        "max_receipt_age_seconds": int(max_receipt_age_seconds),
        "repository_url": _safe_repository_url(repository_url),
    }
    if source_tree is not None:
        result["source_tree"] = str(Path(source_tree).expanduser())
    return result


def _state_slug(value: str) -> str:
    slug = _STATE_SLUG.sub("-", value.casefold()).strip("-")
    return slug or "unknown-state"


def _suggest_preview_path(
    *,
    artifact_root: Path,
    lifecycle_state: str,
) -> Path:
    source_commit, _, _ = SAVE.RENDER._deploy_surface_identity()
    name = (
        "pio-phase2-"
        + _state_slug(lifecycle_state)
        + "-"
        + source_commit[:12]
        + ".preview.json"
    )
    return artifact_root / name


def _preview_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("mutation preview is missing or symlinked")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("mutation preview permissions must be 0600")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _default_execution_receipt(preview: Path) -> Path:
    return preview.with_name(f"{preview.name}.execution.json")


def _result(
    *,
    state: str,
    next_action: str,
    next_tool: str | None,
    next_parameters: dict[str, Any],
    next_mutation_flag: str | None,
    lifecycle: Any,
    preview_path: Path | None = None,
    preview_sha256: str | None = None,
    preview_current: bool | None = None,
    execution_receipt_path: Path | None = None,
    receipt_status: str | None = None,
    receipt_post_mutation_verified: bool | None = None,
    attention_required: bool = True,
    blockers: tuple[str, ...] = (),
    freshness: Any | None = None,
    audit: Any | None = None,
) -> Phase2ArtifactAwareHandoff:
    return Phase2ArtifactAwareHandoff(
        state=state,
        next_action=next_action,
        next_tool=next_tool,
        next_parameters=next_parameters,
        next_mutation_flag=next_mutation_flag,
        lifecycle_state=str(lifecycle.state),
        lifecycle_next_action=str(lifecycle.next_action),
        lifecycle_next_tool=(
            str(lifecycle.next_tool)
            if lifecycle.next_tool is not None
            else None
        ),
        lifecycle_mutation_flag=(
            str(lifecycle.next_mutation_flag)
            if lifecycle.next_mutation_flag is not None
            else None
        ),
        preview_path=str(preview_path) if preview_path is not None else None,
        preview_sha256=preview_sha256,
        preview_current=preview_current,
        execution_receipt_path=(
            str(execution_receipt_path)
            if execution_receipt_path is not None
            else None
        ),
        receipt_status=receipt_status,
        receipt_post_mutation_verified=receipt_post_mutation_verified,
        attention_required=attention_required,
        blockers=blockers,
        lifecycle=lifecycle.to_record(),
        freshness=(
            freshness.to_record()
            if freshness is not None
            else None
        ),
        audit=audit.to_record() if audit is not None else None,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        artifact_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def inspect_artifact_handoff(
    *,
    artifact_root: str | Path = "/var/tmp",
    preview_path: str | Path | None = None,
    execution_receipt_path: str | Path | None = None,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    receipt_path: str | Path = "/opt/pio/data/phase2-isolated-smoke-receipt.json",
    max_receipt_age_seconds: int = 1800,
    source_tree: str | Path | None = None,
    repository_url: str = LIFECYCLE.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
) -> Phase2ArtifactAwareHandoff:
    root = _artifact_root(artifact_root)
    common = _common_parameters(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        receipt_path=receipt_path,
        max_receipt_age_seconds=max_receipt_age_seconds,
        source_tree=source_tree,
        repository_url=repository_url,
    )

    lifecycle = LIFECYCLE.inspect_lifecycle_handoff(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        receipt_path=receipt_path,
        max_receipt_age_seconds=max_receipt_age_seconds,
        source_tree=source_tree,
        repository_url=common["repository_url"],
    )
    if not _lifecycle_boundary_ok(lifecycle):
        raise ValueError("Phase-2 lifecycle handoff crossed the read-only boundary")

    explicit_preview = (
        Path(preview_path).expanduser().resolve(strict=False)
        if preview_path is not None
        else None
    )
    explicit_receipt = (
        Path(execution_receipt_path).expanduser().resolve(strict=False)
        if execution_receipt_path is not None
        else None
    )

    if explicit_receipt is not None:
        try:
            audit = AUDIT.audit_mutation_execution_receipt(
                execution_receipt_path=explicit_receipt,
                runtime_root=runtime_root,
                unit_destination=unit_destination,
                env_file=env_file,
                data_root=data_root,
                receipt_path=receipt_path,
                max_receipt_age_seconds=max_receipt_age_seconds,
                source_tree=source_tree,
                repository_url=common["repository_url"],
            )
        except (OSError, RuntimeError, ValueError):
            return _result(
                state="MUTATION_RECEIPT_REVIEW_REQUIRED",
                next_action="REVIEW_MUTATION_EXECUTION_RECEIPT",
                next_tool="check_phase2_isolated_mutation_execution_receipt.py",
                next_parameters={
                    "execution_receipt": str(explicit_receipt),
                    **common,
                },
                next_mutation_flag=None,
                lifecycle=lifecycle,
                execution_receipt_path=explicit_receipt,
                attention_required=True,
                blockers=("RECEIPT_AUDIT_FAILED",),
            )

        if (
            explicit_preview is not None
            and Path(audit.preview_path).resolve(strict=False)
            != explicit_preview
        ):
            return _result(
                state="MUTATION_ARTIFACT_MISMATCH",
                next_action="REVIEW_MUTATION_ARTIFACT_PAIR",
                next_tool="check_phase2_isolated_mutation_execution_receipt.py",
                next_parameters={
                    "execution_receipt": str(explicit_receipt),
                    **common,
                },
                next_mutation_flag=None,
                lifecycle=lifecycle,
                preview_path=explicit_preview,
                execution_receipt_path=explicit_receipt,
                receipt_status=str(audit.receipt_status),
                receipt_post_mutation_verified=bool(
                    audit.post_mutation_verified
                ),
                attention_required=True,
                blockers=("PREVIEW_RECEIPT_PATH_MISMATCH",),
                audit=audit,
            )

        if str(audit.current_state) != str(lifecycle.state):
            return _result(
                state="LIFECYCLE_CHANGED_DURING_ARTIFACT_HANDOFF",
                next_action="RERUN_ARTIFACT_HANDOFF",
                next_tool="check_phase2_isolated_artifact_handoff.py",
                next_parameters={
                    "execution_receipt": str(explicit_receipt),
                    **common,
                },
                next_mutation_flag=None,
                lifecycle=lifecycle,
                preview_path=Path(audit.preview_path),
                execution_receipt_path=explicit_receipt,
                receipt_status=str(audit.receipt_status),
                receipt_post_mutation_verified=bool(
                    audit.post_mutation_verified
                ),
                attention_required=True,
                blockers=("LIFECYCLE_STATE_RACE",),
                audit=audit,
            )

        if not audit.post_mutation_verified:
            blockers = ["MUTATION_RECEIPT_NOT_VERIFIED"]
            if not audit.audit_integrity_valid:
                blockers.append("MUTATION_ARTIFACT_INTEGRITY_FAILED")
            if not audit.receipt_terminal:
                blockers.append("MUTATION_RECEIPT_NOT_TERMINAL")
            if audit.receipt_terminal and not audit.mutation_succeeded:
                blockers.append("MUTATION_NOT_SUCCESSFUL")
            if (
                audit.mutation_succeeded
                and not audit.post_mutation_progress_observed
            ):
                blockers.append("LIFECYCLE_PROGRESS_NOT_OBSERVED")
            return _result(
                state="MUTATION_RECEIPT_REVIEW_REQUIRED",
                next_action="REVIEW_MUTATION_OUTCOME",
                next_tool="check_phase2_isolated_mutation_execution_receipt.py",
                next_parameters={
                    "execution_receipt": str(explicit_receipt),
                    **common,
                },
                next_mutation_flag=None,
                lifecycle=lifecycle,
                preview_path=Path(audit.preview_path),
                execution_receipt_path=explicit_receipt,
                receipt_status=str(audit.receipt_status),
                receipt_post_mutation_verified=False,
                attention_required=True,
                blockers=tuple(blockers),
                audit=audit,
            )

        if lifecycle.next_mutation_flag is not None:
            suggested = _suggest_preview_path(
                artifact_root=root,
                lifecycle_state=str(lifecycle.state),
            )
            return _result(
                state="NEXT_MUTATION_PREVIEW_REQUIRED",
                next_action="SAVE_NEXT_REVIEWED_MUTATION_PREVIEW",
                next_tool="save_phase2_isolated_mutation_preview.py",
                next_parameters={
                    "output": str(suggested),
                    "replace": False,
                    **common,
                },
                next_mutation_flag=None,
                lifecycle=lifecycle,
                preview_path=Path(audit.preview_path),
                execution_receipt_path=explicit_receipt,
                receipt_status=str(audit.receipt_status),
                receipt_post_mutation_verified=True,
                attention_required=True,
                blockers=(),
                audit=audit,
            )

        return _result(
            state="MUTATION_AUDITED_NO_MUTATION_PENDING",
            next_action=str(lifecycle.next_action),
            next_tool=(
                str(lifecycle.next_tool)
                if lifecycle.next_tool is not None
                else None
            ),
            next_parameters=dict(lifecycle.next_parameters),
            next_mutation_flag=None,
            lifecycle=lifecycle,
            preview_path=Path(audit.preview_path),
            execution_receipt_path=explicit_receipt,
            receipt_status=str(audit.receipt_status),
            receipt_post_mutation_verified=True,
            attention_required=bool(lifecycle.attention_required),
            blockers=tuple(str(item) for item in lifecycle.blockers),
            audit=audit,
        )

    effective_preview = explicit_preview
    if effective_preview is None and lifecycle.next_mutation_flag is not None:
        candidate = _suggest_preview_path(
            artifact_root=root,
            lifecycle_state=str(lifecycle.state),
        )
        if candidate.exists():
            effective_preview = candidate
        else:
            return _result(
                state="MUTATION_PREVIEW_REQUIRED",
                next_action="SAVE_REVIEWED_MUTATION_PREVIEW",
                next_tool="save_phase2_isolated_mutation_preview.py",
                next_parameters={
                    "output": str(candidate),
                    "replace": False,
                    **common,
                },
                next_mutation_flag=None,
                lifecycle=lifecycle,
                attention_required=True,
                blockers=(),
            )

    if effective_preview is not None:
        auto_receipt = _default_execution_receipt(effective_preview)
        if auto_receipt.exists():
            return inspect_artifact_handoff(
                artifact_root=root,
                preview_path=effective_preview,
                execution_receipt_path=auto_receipt,
                runtime_root=runtime_root,
                unit_destination=unit_destination,
                env_file=env_file,
                data_root=data_root,
                receipt_path=receipt_path,
                max_receipt_age_seconds=max_receipt_age_seconds,
                source_tree=source_tree,
                repository_url=common["repository_url"],
            )

        try:
            freshness = FRESH.check_mutation_preview_freshness(
                preview_path=effective_preview,
                runtime_root=runtime_root,
                unit_destination=unit_destination,
                env_file=env_file,
                data_root=data_root,
                receipt_path=receipt_path,
                max_receipt_age_seconds=max_receipt_age_seconds,
                source_tree=source_tree,
                repository_url=common["repository_url"],
            )
        except (OSError, RuntimeError, ValueError):
            return _result(
                state="MUTATION_PREVIEW_REVIEW_REQUIRED",
                next_action="REVIEW_MUTATION_PREVIEW",
                next_tool="check_phase2_isolated_mutation_freshness.py",
                next_parameters={
                    "preview": str(effective_preview),
                    **common,
                },
                next_mutation_flag=None,
                lifecycle=lifecycle,
                preview_path=effective_preview,
                attention_required=True,
                blockers=("PREVIEW_FRESHNESS_CHECK_FAILED",),
            )

        if not freshness.preview_current:
            return _result(
                state="MUTATION_PREVIEW_REGENERATION_REQUIRED",
                next_action="REPLACE_STALE_MUTATION_PREVIEW",
                next_tool="save_phase2_isolated_mutation_preview.py",
                next_parameters={
                    "output": str(effective_preview),
                    "replace": True,
                    **common,
                },
                next_mutation_flag=None,
                lifecycle=lifecycle,
                preview_path=effective_preview,
                preview_current=False,
                attention_required=True,
                blockers=(str(freshness.status),),
                freshness=freshness,
            )

        preview_sha = _preview_sha256(effective_preview)
        return _result(
            state="MUTATION_EXECUTION_READY",
            next_action="EXECUTE_FRESH_MUTATION_WITH_RECEIPT",
            next_tool="run_phase2_isolated_mutation_with_receipt.py",
            next_parameters={
                "preview": str(effective_preview),
                "expected_preview_sha256": preview_sha,
                "execution_receipt": str(auto_receipt),
                **common,
            },
            next_mutation_flag="--execute",
            lifecycle=lifecycle,
            preview_path=effective_preview,
            preview_sha256=preview_sha,
            preview_current=True,
            execution_receipt_path=auto_receipt,
            attention_required=True,
            blockers=(),
            freshness=freshness,
        )

    return _result(
        state="NO_MUTATION_ARTIFACT_REQUIRED",
        next_action=str(lifecycle.next_action),
        next_tool=(
            str(lifecycle.next_tool)
            if lifecycle.next_tool is not None
            else None
        ),
        next_parameters=dict(lifecycle.next_parameters),
        next_mutation_flag=None,
        lifecycle=lifecycle,
        attention_required=bool(lifecycle.attention_required),
        blockers=tuple(str(item) for item in lifecycle.blockers),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Route the current isolated Phase-2 operator workflow across saved "
            "mutation previews and execution receipts without performing any "
            "artifact write, RPC call, or mutation."
        )
    )
    parser.add_argument("--artifact-root", default="/var/tmp")
    parser.add_argument("--preview")
    parser.add_argument("--execution-receipt")
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
        default=LIFECYCLE.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    )
    args = parser.parse_args()

    report = inspect_artifact_handoff(
        artifact_root=args.artifact_root,
        preview_path=args.preview,
        execution_receipt_path=args.execution_receipt,
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        receipt_path=args.receipt_path,
        max_receipt_age_seconds=args.max_receipt_age_seconds,
        source_tree=args.source_tree,
        repository_url=args.repository_url,
    )
    import json
    print(json.dumps(report.to_record(), indent=2))
    if report.attention_required:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
