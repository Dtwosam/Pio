from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_CONFLICT_EVIDENCE_V1"

TARGET_POOL = "54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ"

READINESS_TOOL = Path("deploy/tools/check_manual_market_paper_readiness.py")
COLLECTION_TOOL = Path("deploy/tools/apply_phase2_collection_stack.py")
STATE_READER_TOOL = Path("deploy/tools/apply_phase2_single_slot_stack_patch.py")
PHASE2_MANIFEST = Path("deploy/manifests/market-paper-phase2-prerequisites.json")
MARKET_PAPER_MANIFEST = Path("deploy/manifests/market-paper-manual-cycle.json")
STATE_READER_PATCH = Path("deploy/patches/phase2-single-slot-stack-state-reader.patch")

REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "f50dd02d650902596d4e665f22231822d2a9a74f",
    COLLECTION_TOOL: "a8a76cd4db95867a842de93f8688daa0cd7100c3",
    STATE_READER_TOOL: "a2e4e0c0435f15be7126e253d1861955be6c2ecd",
    PHASE2_MANIFEST: "dfb1eb62241f6fc3b6351e3b40b524fb68f4a61e",
    MARKET_PAPER_MANIFEST: "1891afe2c88e267a3dd76fdcdb717a49bc94b15c",
    STATE_READER_PATCH: "330e2c33956f8a96850a1e072d6a2fa0a4d619af",
}

IDENTITY_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "repository",
    "target_pool",
    "target_pool_cursor",
    "detector_service",
    "watcher_service",
    "git_metadata",
    "phase2",
    "state_reader",
    "market_paper",
    "conflict_paths",
    "evidence_complete",
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


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
            raise ValueError(f"reviewed conflict-evidence artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed conflict-evidence artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed conflict-evidence artifact mismatch: {relative}")


def _stderr_text(proc: subprocess.CompletedProcess[Any]) -> str:
    stderr = proc.stderr
    if stderr is None:
        return ""
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", errors="replace")
    return str(stderr)


def _classify_git_error(proc: subprocess.CompletedProcess[Any]) -> str | None:
    if proc.returncode == 0:
        return None
    text = _stderr_text(proc).lower()
    if "dubious ownership" in text or "safe.directory" in text:
        return "GIT_SAFE_DIRECTORY"
    if "not a git repository" in text:
        return "NOT_GIT_REPOSITORY"
    if "permission denied" in text:
        return "PERMISSION_DENIED"
    if "detected dubious ownership" in text:
        return "GIT_SAFE_DIRECTORY"
    return "GIT_READ_FAILED"


def _run_git(
    repo: Path,
    args: list[str],
    *,
    safe_directory_override: bool,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> subprocess.CompletedProcess[Any]:
    command = ["git"]
    if safe_directory_override:
        command.extend(["-c", f"safe.directory={repo}"])
    command.extend(args)
    return runner(
        command,
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )


def _probe_git_read(
    repo: Path,
    args: list[str],
    *,
    parser: Callable[[str], Any],
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> dict[str, Any]:
    default = _run_git(
        repo,
        args,
        safe_directory_override=False,
        runner=runner,
    )
    default_ok = default.returncode == 0
    override_attempted = not default_ok
    override = None
    selected = default

    if override_attempted:
        override = _run_git(
            repo,
            args,
            safe_directory_override=True,
            runner=runner,
        )
        if override.returncode == 0:
            selected = override

    value = None
    if selected.returncode == 0:
        try:
            value = parser(str(selected.stdout))
        except Exception:
            value = None

    return {
        "value": value,
        "default_ok": default_ok,
        "default_returncode": int(default.returncode),
        "default_error_category": _classify_git_error(default),
        "safe_directory_override_attempted": override_attempted,
        "override_ok": None if override is None else override.returncode == 0,
        "override_returncode": None if override is None else int(override.returncode),
        "override_error_category": None if override is None else _classify_git_error(override),
        "read_ok": selected.returncode == 0 and value is not None,
    }


def _git_metadata(
    repo: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> dict[str, Any]:
    head = _probe_git_read(
        repo,
        ["rev-parse", "HEAD"],
        parser=lambda value: value.strip() or None,
        runner=runner,
    )
    status = _probe_git_read(
        repo,
        ["status", "--porcelain=v1", "--untracked-files=no"],
        parser=lambda value: sum(
            1 for line in value.splitlines() if line.strip()
        ),
        runner=runner,
    )
    return {
        "production_head": head["value"],
        "tracked_changes": status["value"],
        "head_read": {key: value for key, value in head.items() if key != "value"},
        "status_read": {key: value for key, value in status.items() if key != "value"},
        "metadata_readable": bool(head["read_ok"] and status["read_ok"]),
        "safe_directory_override_recovered": bool(
            (not head["default_ok"] and head["read_ok"])
            or (not status["default_ok"] and status["read_ok"])
        ),
    }


def _file_digest_record(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        return {
            "exists": True,
            "regular_file": False,
            "symlink": True,
            "git_blob": None,
            "sha256": None,
            "size": None,
        }
    if not path.exists():
        return {
            "exists": False,
            "regular_file": False,
            "symlink": False,
            "git_blob": None,
            "sha256": None,
            "size": None,
        }
    if not path.is_file():
        return {
            "exists": True,
            "regular_file": False,
            "symlink": False,
            "git_blob": None,
            "sha256": None,
            "size": None,
        }
    return {
        "exists": True,
        "regular_file": True,
        "symlink": False,
        "git_blob": _git_blob_sha(path),
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _collection_evidence(
    collection_module: Any,
    *,
    repository: Path,
    source: Path,
    manifest: Path,
) -> dict[str, Any]:
    report = collection_module.preflight_collection_stack(
        repository=repository,
        source_tree=source,
        manifest=source / manifest,
    )

    files = []
    for item in report.files:
        target = repository / item.path
        source_file = source / item.path
        files.append(
            {
                "path": item.path,
                "status": item.status,
                "expected_base_blob": item.expected_base_blob,
                "target_blob": item.target_blob,
                "source_blob": item.source_blob,
                "current_blob": item.current_blob,
                "current": _file_digest_record(target),
                "source": _file_digest_record(source_file),
            }
        )

    return {
        "content_ready": bool(report.content_ready),
        "apply_authorized": bool(report.apply_authorized),
        "production_deployment_authorized": bool(
            report.production_deployment_authorized
        ),
        "deployment_guard_apply_locked": bool(
            report.deployment_guard_apply_locked
        ),
        "files_changed": int(report.files_changed),
        "files": files,
    }


def _state_reader_evidence(
    state_module: Any,
    *,
    repository: Path,
    source: Path,
) -> dict[str, Any]:
    relative = Path(str(state_module.TARGET_PATH))
    target = repository / relative
    source_target = source / relative
    patch = source / STATE_READER_PATCH

    current = _file_digest_record(target)
    reviewed_target = _file_digest_record(source_target)
    current_blob = current["git_blob"]

    if current["symlink"]:
        status = "CONFLICT_SYMLINK"
    elif not current["exists"]:
        status = "CONFLICT_MISSING"
    elif not current["regular_file"]:
        status = "CONFLICT_NON_FILE"
    elif current_blob == str(state_module.STACK_TARGET_BLOB_SHA):
        status = "ALREADY_TARGET"
    elif current_blob == str(state_module.STACK_BASE_BLOB_SHA):
        try:
            result = state_module.apply_guarded_patch(
                repository=repository,
                patch=patch,
                reference_patch=patch,
                apply=False,
            )
            status = str(result.status)
        except Exception:
            status = "PATCH_CHECK_FAILED"
    else:
        status = "CONFLICT_MODIFIED"

    patch_scope = None
    patch_sha256 = None
    if patch.is_file() and not patch.is_symlink():
        patch_sha256 = _sha256(patch)
        try:
            patch_scope = list(
                state_module.changed_paths(
                    patch.read_text(encoding="utf-8")
                )
            )
        except Exception:
            patch_scope = None

    ready = status in {"READY_UPDATE", "ALREADY_TARGET"}
    return {
        "path": str(relative),
        "status": status,
        "preflight_ready": ready,
        "expected_base_blob": str(state_module.STACK_BASE_BLOB_SHA),
        "target_blob": str(state_module.STACK_TARGET_BLOB_SHA),
        "current": current,
        "reviewed_target": reviewed_target,
        "patch_sha256": patch_sha256,
        "patch_scope": patch_scope,
        "patch_matches_reviewed_source_blob": (
            _git_blob_sha(patch) == REVIEWED_SOURCE_BLOBS[STATE_READER_PATCH]
            if patch.is_file() and not patch.is_symlink()
            else False
        ),
    }


def _conflict_paths(
    phase2: dict[str, Any],
    state_reader: dict[str, Any],
    market_paper: dict[str, Any],
) -> list[dict[str, str]]:
    conflicts: list[dict[str, str]] = []
    ready_collection_statuses = {"ALREADY_TARGET", "READY_CREATE", "READY_UPDATE"}
    for layer, payload in (
        ("PHASE2_SHARED_PREREQUISITES", phase2),
        ("MARKET_PAPER_RUNTIME", market_paper),
    ):
        for item in payload["files"]:
            if item["status"] not in ready_collection_statuses:
                conflicts.append(
                    {
                        "layer": layer,
                        "path": item["path"],
                        "status": item["status"],
                    }
                )

    if state_reader["status"] not in {"ALREADY_TARGET", "READY_UPDATE"}:
        conflicts.append(
            {
                "layer": "STATE_READER",
                "path": state_reader["path"],
                "status": state_reader["status"],
            }
        )

    return conflicts


def validate_conflict_evidence(evidence: dict[str, Any]) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("conflict evidence must be a JSON object")

    expected_keys = set(IDENTITY_FIELDS) | {"evidence_sha256"}
    if set(evidence) != expected_keys:
        raise ValueError("conflict evidence fields do not match reviewed schema")
    if evidence.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported conflict evidence format")
    if evidence.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected conflict evidence artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if evidence.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("conflict evidence source lineage mismatch")

    repository = evidence.get("repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("conflict evidence repository must be absolute")

    if not isinstance(evidence.get("target_pool"), str) or not evidence["target_pool"]:
        raise ValueError("conflict evidence target pool is invalid")
    cursor = evidence.get("target_pool_cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise ValueError("conflict evidence cursor is invalid")

    for field in ("detector_service", "watcher_service"):
        if not isinstance(evidence.get(field), str) or not evidence[field]:
            raise ValueError(f"conflict evidence {field} is invalid")

    git_metadata = evidence.get("git_metadata")
    if not isinstance(git_metadata, dict):
        raise ValueError("conflict evidence git metadata is missing")
    if not isinstance(git_metadata.get("metadata_readable"), bool):
        raise ValueError("conflict evidence git metadata readability is invalid")
    if not isinstance(git_metadata.get("safe_directory_override_recovered"), bool):
        raise ValueError("conflict evidence git safe-directory recovery flag is invalid")

    for layer in ("phase2", "market_paper"):
        payload = evidence.get(layer)
        if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
            raise ValueError(f"conflict evidence {layer} payload is invalid")
        for item in payload["files"]:
            if not isinstance(item, dict):
                raise ValueError(f"conflict evidence {layer} file is invalid")
            if not isinstance(item.get("path"), str) or not item["path"]:
                raise ValueError(f"conflict evidence {layer} file path is invalid")
            if not isinstance(item.get("status"), str) or not item["status"]:
                raise ValueError(f"conflict evidence {layer} file status is invalid")
            for digest_field in (
                "expected_base_blob",
                "target_blob",
                "source_blob",
                "current_blob",
            ):
                value = item.get(digest_field)
                if value is not None and not _is_hex_digest(value, 40):
                    raise ValueError(
                        f"conflict evidence {layer} {digest_field} is invalid"
                    )

    state_reader = evidence.get("state_reader")
    if not isinstance(state_reader, dict):
        raise ValueError("conflict evidence state-reader payload is invalid")
    for field in ("expected_base_blob", "target_blob"):
        if not _is_hex_digest(state_reader.get(field), 40):
            raise ValueError(f"conflict evidence state-reader {field} is invalid")
    patch_sha = state_reader.get("patch_sha256")
    if patch_sha is not None and not _is_hex_digest(patch_sha, 64):
        raise ValueError("conflict evidence patch digest is invalid")

    conflicts = evidence.get("conflict_paths")
    if not isinstance(conflicts, list):
        raise ValueError("conflict evidence conflict_paths must be a list")
    for item in conflicts:
        if (
            not isinstance(item, dict)
            or set(item) != {"layer", "path", "status"}
            or not all(isinstance(item[field], str) and item[field] for field in item)
        ):
            raise ValueError("conflict evidence conflict entry is invalid")

    expected_complete = bool(
        git_metadata["metadata_readable"]
        and evidence["detector_service"] != "unknown"
        and evidence["watcher_service"] != "unknown"
    )
    if evidence.get("evidence_complete") is not expected_complete:
        raise ValueError("conflict evidence completeness flag mismatch")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if evidence.get(field) is not False:
            raise ValueError(f"conflict evidence requires {field}=false")

    if not _is_hex_digest(evidence.get("evidence_sha256"), 64):
        raise ValueError("conflict evidence digest is invalid")
    identity = {field: evidence[field] for field in IDENTITY_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if evidence["evidence_sha256"] != expected_digest:
        raise ValueError("conflict evidence digest mismatch")


def build_conflict_evidence(
    *,
    repository: str | Path,
    source_tree: str | Path,
    pool: str = TARGET_POOL,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> dict[str, Any]:
    repository_path = Path(repository).resolve()
    source = Path(source_tree).resolve()

    if not repository_path.is_dir():
        raise ValueError(f"production repository is missing: {repository_path}")
    if not (repository_path / ".git").exists():
        raise ValueError(f"production repository is not a git tree: {repository_path}")
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")

    _verify_reviewed_source(source)

    readiness_module = _load_module(
        source / READINESS_TOOL,
        "manual_market_paper_conflict_readiness",
    )
    collection_module = _load_module(
        source / COLLECTION_TOOL,
        "manual_market_paper_conflict_collection",
    )
    state_module = _load_module(
        source / STATE_READER_TOOL,
        "manual_market_paper_conflict_state_reader",
    )

    git_metadata = _git_metadata(repository_path, runner=runner)
    phase2 = _collection_evidence(
        collection_module,
        repository=repository_path,
        source=source,
        manifest=PHASE2_MANIFEST,
    )
    market_paper = _collection_evidence(
        collection_module,
        repository=repository_path,
        source=source,
        manifest=MARKET_PAPER_MANIFEST,
    )
    state_reader = _state_reader_evidence(
        state_module,
        repository=repository_path,
        source=source,
    )

    detector_service = readiness_module._service_state(
        readiness_module.DETECTOR_SERVICE,
        runner=runner,
    )
    watcher_service = readiness_module._service_state(
        readiness_module.WATCHER_SERVICE,
        runner=runner,
    )
    target_cursor = readiness_module._target_cursor(
        repository_path,
        pool=pool,
    )

    conflicts = _conflict_paths(phase2, state_reader, market_paper)
    evidence_complete = bool(
        git_metadata["metadata_readable"]
        and detector_service != "unknown"
        and watcher_service != "unknown"
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
        "repository": str(repository_path),
        "target_pool": pool,
        "target_pool_cursor": target_cursor,
        "detector_service": detector_service,
        "watcher_service": watcher_service,
        "git_metadata": git_metadata,
        "phase2": phase2,
        "state_reader": state_reader,
        "market_paper": market_paper,
        "conflict_paths": conflicts,
        "evidence_complete": evidence_complete,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    evidence = {
        **identity,
        "evidence_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }
    validate_conflict_evidence(evidence)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Collect sealed read-only diagnostics for manual market/PAPER "
            "deployment conflicts. The tool records only paths, status, hashes, "
            "sizes, service state, cursor state, and categorized Git-read "
            "failures; it never copies, patches, deletes, resets, restarts, "
            "moves detector state, enables PAPER, or authorizes mutation."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--pool", default=TARGET_POOL)
    args = parser.parse_args()

    evidence = build_conflict_evidence(
        repository=args.repo,
        source_tree=args.source_tree,
        pool=args.pool,
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
