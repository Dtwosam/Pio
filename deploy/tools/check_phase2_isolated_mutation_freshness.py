#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
RENDER_TOOL = TOOLS_DIR / "render_phase2_isolated_mutation_command.py"
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_MAX_PREVIEW_BYTES = 2 * 1024 * 1024


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RENDER = _load(RENDER_TOOL, "phase2_mutation_freshness_renderer")


@dataclass(frozen=True)
class Phase2MutationPreviewFreshness:
    preview_path: str
    format_version: int
    fingerprint_schema: str
    prior_reviewed_source_commit: str
    current_reviewed_source_commit: str
    reviewed_source_commit_matches: bool
    prior_deploy_surface_sha256: str
    current_deploy_surface_sha256: str
    deploy_surface_sha256_matches: bool
    prior_deploy_surface_files: int
    current_deploy_surface_files: int
    deploy_surface_files_match: bool
    status: str
    preview_current: bool
    prior_state: str
    current_state: str
    state_matches: bool
    action_matches: bool
    tool_matches: bool
    mutation_argv_matches: bool
    prior_mutation_tool_sha256: str
    current_mutation_tool_sha256: str | None
    mutation_tool_sha256_matches: bool
    prior_preflight_fingerprint: str
    current_preflight_fingerprint: str
    preflight_fingerprint_matches: bool
    prior_mutation_fingerprint: str
    current_mutation_fingerprint: str | None
    mutation_fingerprint_matches: bool
    current_preflight_succeeded: bool
    current_mutation_rendered: bool
    current_preview: dict[str, Any]
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    daemon_reload_performed: bool
    production_tree_modified: bool
    mutation_executed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _regular_preview_file(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise ValueError("mutation preview path must not be a symlink")
    if not path.is_file():
        raise ValueError(f"mutation preview file is missing: {path}")
    if path.stat().st_size > _MAX_PREVIEW_BYTES:
        raise ValueError("mutation preview file is too large")
    return path.resolve()


def _load_prior_preview(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("mutation preview file is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("mutation preview JSON must be an object")

    if payload.get("format_version") != RENDER.MUTATION_PREVIEW_FORMAT_VERSION:
        raise ValueError("prior mutation preview format version is unsupported")
    if payload.get("fingerprint_schema") != RENDER.MUTATION_FINGERPRINT_SCHEMA:
        raise ValueError("prior mutation preview fingerprint schema is unsupported")
    source_commit = payload.get("reviewed_source_commit")
    deploy_surface_sha256 = payload.get("deploy_surface_sha256")
    deploy_surface_files = payload.get("deploy_surface_files")
    if (
        not isinstance(source_commit, str)
        or not _COMMIT.fullmatch(source_commit)
    ):
        raise ValueError("prior reviewed source commit is invalid")
    if (
        not isinstance(deploy_surface_sha256, str)
        or not _FINGERPRINT.fullmatch(deploy_surface_sha256)
    ):
        raise ValueError("prior deploy surface SHA256 is invalid")
    if (
        isinstance(deploy_surface_files, bool)
        or not isinstance(deploy_surface_files, int)
        or deploy_surface_files <= 0
    ):
        raise ValueError("prior deploy surface file count is invalid")

    if not bool(payload.get("read_only")):
        raise ValueError("prior mutation preview is not read-only")
    for key in (
        "rpc_called",
        "database_write_performed",
        "service_control_performed",
        "daemon_reload_performed",
        "production_tree_modified",
    ):
        if bool(payload.get(key, False)):
            raise ValueError("prior mutation preview crossed the read-only boundary")

    if not bool(payload.get("preflight_succeeded")):
        raise ValueError("prior mutation preview did not have a successful preflight")
    if not bool(payload.get("mutation_rendered")):
        raise ValueError("prior mutation preview did not render a mutation")
    if bool(payload.get("mutation_executed")):
        raise ValueError("prior mutation preview reports mutation execution")

    preflight_fp = payload.get("preflight_fingerprint")
    mutation_fp = payload.get("mutation_fingerprint")
    if not isinstance(preflight_fp, str) or not _FINGERPRINT.fullmatch(preflight_fp):
        raise ValueError("prior preflight fingerprint is invalid")
    if not isinstance(mutation_fp, str) or not _FINGERPRINT.fullmatch(mutation_fp):
        raise ValueError("prior mutation fingerprint is invalid")

    mutation_argv = payload.get("mutation_argv")
    if not isinstance(mutation_argv, list) or not mutation_argv:
        raise ValueError("prior mutation argv is invalid")
    if not all(isinstance(value, str) for value in mutation_argv):
        raise ValueError("prior mutation argv contains non-string values")
    if mutation_argv[-1] not in {"--apply", "--prepare"}:
        raise ValueError("prior mutation argv is missing reviewed mutation flag")

    mutation_tool_sha256 = payload.get("mutation_tool_sha256")
    if (
        not isinstance(mutation_tool_sha256, str)
        or not _FINGERPRINT.fullmatch(mutation_tool_sha256)
    ):
        raise ValueError("prior mutation tool SHA256 is invalid")

    for key in ("state", "next_action", "next_tool"):
        if not isinstance(payload.get(key), str):
            raise ValueError(f"prior mutation preview is missing {key}")

    return payload


def _current_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "format_version", None)
        == RENDER.MUTATION_PREVIEW_FORMAT_VERSION
        and getattr(report, "fingerprint_schema", None)
        == RENDER.MUTATION_FINGERPRINT_SCHEMA
        and isinstance(getattr(report, "reviewed_source_commit", None), str)
        and _COMMIT.fullmatch(getattr(report, "reviewed_source_commit"))
        and isinstance(getattr(report, "deploy_surface_sha256", None), str)
        and _FINGERPRINT.fullmatch(getattr(report, "deploy_surface_sha256"))
        and isinstance(getattr(report, "deploy_surface_files", None), int)
        and not isinstance(getattr(report, "deploy_surface_files", None), bool)
        and getattr(report, "deploy_surface_files") > 0
        and (
            (
                not getattr(report, "mutation_rendered", False)
                and getattr(report, "mutation_tool_sha256", None) is None
            )
            or (
                getattr(report, "mutation_rendered", False)
                and isinstance(
                    getattr(report, "mutation_tool_sha256", None),
                    str,
                )
                and _FINGERPRINT.fullmatch(
                    getattr(report, "mutation_tool_sha256")
                )
            )
        )
        and getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "daemon_reload_performed", True)
        and not getattr(report, "production_tree_modified", True)
        and not getattr(report, "mutation_executed", True)
    )


def check_mutation_preview_freshness(
    *,
    preview_path: str | Path,
    timeout_seconds: int = 60,
    **handoff_kwargs: Any,
) -> Phase2MutationPreviewFreshness:
    path = _regular_preview_file(preview_path)
    prior = _load_prior_preview(path)

    current = RENDER.render_reviewed_mutation_command(
        timeout_seconds=timeout_seconds,
        **handoff_kwargs,
    )
    if not _current_boundary_ok(current):
        raise ValueError("current mutation preview crossed the read-only boundary")

    current_record = current.to_record()
    current_argv = (
        list(current.mutation_argv)
        if current.mutation_argv is not None
        else None
    )

    reviewed_source_commit_matches = (
        current.reviewed_source_commit == prior["reviewed_source_commit"]
    )
    deploy_surface_sha256_matches = (
        current.deploy_surface_sha256 == prior["deploy_surface_sha256"]
    )
    deploy_surface_files_match = (
        current.deploy_surface_files == prior["deploy_surface_files"]
    )
    state_matches = str(current.state) == str(prior["state"])
    action_matches = str(current.next_action) == str(prior["next_action"])
    tool_matches = str(current.next_tool) == str(prior["next_tool"])
    argv_matches = current_argv == prior["mutation_argv"]
    mutation_tool_sha256_matches = (
        current.mutation_tool_sha256 == prior["mutation_tool_sha256"]
    )
    preflight_fp_matches = (
        current.preflight_fingerprint == prior["preflight_fingerprint"]
    )
    mutation_fp_matches = (
        current.mutation_fingerprint == prior["mutation_fingerprint"]
    )

    current_ready = bool(
        current.preflight_succeeded
        and current.mutation_rendered
        and current.mutation_fingerprint is not None
    )
    preview_current = bool(
        current_ready
        and reviewed_source_commit_matches
        and deploy_surface_sha256_matches
        and deploy_surface_files_match
        and state_matches
        and action_matches
        and tool_matches
        and argv_matches
        and mutation_tool_sha256_matches
        and preflight_fp_matches
        and mutation_fp_matches
    )

    if preview_current:
        status = "CURRENT"
    elif not current_ready:
        status = "CURRENT_PREFLIGHT_NOT_MUTATION_READY"
    else:
        status = "STALE"

    return Phase2MutationPreviewFreshness(
        preview_path=str(path),
        format_version=RENDER.MUTATION_PREVIEW_FORMAT_VERSION,
        fingerprint_schema=RENDER.MUTATION_FINGERPRINT_SCHEMA,
        prior_reviewed_source_commit=str(prior["reviewed_source_commit"]),
        current_reviewed_source_commit=str(current.reviewed_source_commit),
        reviewed_source_commit_matches=reviewed_source_commit_matches,
        prior_deploy_surface_sha256=str(prior["deploy_surface_sha256"]),
        current_deploy_surface_sha256=str(current.deploy_surface_sha256),
        deploy_surface_sha256_matches=deploy_surface_sha256_matches,
        prior_deploy_surface_files=int(prior["deploy_surface_files"]),
        current_deploy_surface_files=int(current.deploy_surface_files),
        deploy_surface_files_match=deploy_surface_files_match,
        status=status,
        preview_current=preview_current,
        prior_state=str(prior["state"]),
        current_state=str(current.state),
        state_matches=state_matches,
        action_matches=action_matches,
        tool_matches=tool_matches,
        mutation_argv_matches=argv_matches,
        prior_mutation_tool_sha256=str(prior["mutation_tool_sha256"]),
        current_mutation_tool_sha256=current.mutation_tool_sha256,
        mutation_tool_sha256_matches=mutation_tool_sha256_matches,
        prior_preflight_fingerprint=str(prior["preflight_fingerprint"]),
        current_preflight_fingerprint=str(current.preflight_fingerprint),
        preflight_fingerprint_matches=preflight_fp_matches,
        prior_mutation_fingerprint=str(prior["mutation_fingerprint"]),
        current_mutation_fingerprint=current.mutation_fingerprint,
        mutation_fingerprint_matches=mutation_fp_matches,
        current_preflight_succeeded=bool(current.preflight_succeeded),
        current_mutation_rendered=bool(current.mutation_rendered),
        current_preview=current_record,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        daemon_reload_performed=False,
        production_tree_modified=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Re-run the guarded Phase-2 read-only preflight and verify that "
            "a previously rendered mutation preview is still current. "
            "No mutation is executed."
        )
    )
    parser.add_argument("--preview", required=True)
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
        default=(
            RENDER.RUNNER.RENDER.HANDOFF.BOOTSTRAP.DEFAULT_REPOSITORY_URL
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()

    report = check_mutation_preview_freshness(
        preview_path=args.preview,
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
    if not report.preview_current:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
