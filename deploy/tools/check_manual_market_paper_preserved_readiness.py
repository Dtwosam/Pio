from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any, Callable


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_READINESS_V1"

TARGET_POOL = "54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ"
DETECTOR_SERVICE = "pio-phase2-add-detector.service"
WATCHER_SERVICE = "pio-phase2-prestate-watch.service"
PAPER_SERVICE_TEMPLATE = "pio-paper@{account}.service"
PAPER_TIMER_TEMPLATE = "pio-paper@{account}.timer"
DETECTOR_STATE = Path("data/phase2-add-detector-state.json")

PRESERVED_BUNDLE_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_source_bundle.py"
)
COLLECTION_TOOL = Path("deploy/tools/apply_phase2_collection_stack.py")
PHASE2_MANIFEST = Path("deploy/manifests/market-paper-phase2-prerequisites.json")
RUNTIME_MANIFEST = Path("deploy/manifests/market-paper-manual-cycle.json")

REVIEWED_SOURCE_BLOBS = {
    PRESERVED_BUNDLE_TOOL: "27c2d87bab3e896d84c22cdef8bd95082ca0cad3",
    COLLECTION_TOOL: "a8a76cd4db95867a842de93f8688daa0cd7100c3",
    PHASE2_MANIFEST: "dfb1eb62241f6fc3b6351e3b40b524fb68f4a61e",
    RUNTIME_MANIFEST: "1891afe2c88e267a3dd76fdcdb717a49bc94b15c",
}

EXPECTED_PRODUCTION_HEAD = "ebc0b3c8405da30d88a5ee156f31bf041ffb1ad8"

RESEARCH_STORE_PATH = "python-learner/src/meteora_learner/research_store.py"
STORAGE_PATH = "python-learner/src/meteora_learner/storage.py"
STATE_READER_PATH = "rust-executor/src/state_reader.rs"

EXPECTED_RESEARCH_STORE_CURRENT_BLOB = "f9deb47c10c88a4e1e364dd12d6c7569c3826a98"
EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB = "31bd88e3d74490f5d0b617ff4b36383e7e12e18f"
EXPECTED_STATE_READER_CURRENT_BLOB = "d1267db6708b91bc8cacabffcd397a380866c79a"
EXPECTED_STATE_READER_CANDIDATE_BLOB = "f54a1021cf8f89d285bde957d1f72d81857ec2fa"

READY_STATUSES = {"ALREADY_TARGET", "READY_CREATE", "READY_UPDATE"}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "repository",
    "reviewed_source_tree",
    "private_bundle_dir",
    "private_bundle_sha256",
    "private_bundle_verified",
    "production_head",
    "production_head_matches_reviewed_baseline",
    "tracked_changes",
    "detector_service",
    "watcher_service",
    "paper_account",
    "paper_service",
    "paper_timer",
    "manual_mode_safe",
    "target_pool",
    "target_pool_cursor",
    "phase2",
    "state_reader",
    "market_paper",
    "deployment_preflight_clean",
    "runtime_files_deployed",
    "operational_services_healthy",
    "manual_paper_runtime_ready",
    "requires_fresh_handoff",
    "requires_preservation_aware_plan",
    "requires_separate_mutation_authorization",
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)


@dataclass(frozen=True)
class FileStatus:
    path: str
    expected_current_blob: str | None
    target_blob: str
    source_blob: str | None
    current_blob: str | None
    status: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LayerSummary:
    content_ready: bool
    deployed: bool
    apply_authorized: bool
    files_changed: int
    status_counts: dict[str, int]
    pending: tuple[dict[str, str], ...]
    nonready: tuple[dict[str, str], ...]
    files: tuple[FileStatus, ...]

    def to_record(self) -> dict[str, Any]:
        value = asdict(self)
        return value


@dataclass(frozen=True)
class StateReaderSummary:
    content_ready: bool
    deployed: bool
    status: str
    expected_current_blob: str
    target_blob: str
    source_blob: str | None
    current_blob: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


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


def _git_blob_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    return _git_blob_sha_bytes(path.read_bytes())


def _is_hex_digest(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe path: {value}")
    normalized = str(path)
    if normalized != value:
        raise ValueError(f"non-normalized path: {value}")
    return normalized


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"JSON artifact must not be a symlink: {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"JSON artifact must be a regular file: {path}")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _verify_reviewed_source(source: Path) -> None:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if not path.is_file():
            raise ValueError(f"reviewed readiness artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed readiness artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed readiness artifact mismatch: {relative}")


def _resolves_within(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _has_symlink_parent(root: Path, path: Path) -> bool:
    root_resolved = root.resolve()
    current = path.parent
    while current != root:
        if current.is_symlink():
            return True
        if current == current.parent:
            return True
        current = current.parent
    return root.is_symlink() or root.resolve() != root_resolved


def _run_read_only(
    args: list[str],
    *,
    cwd: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(
        args,
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )


def _git_read(
    repository: Path,
    args: list[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return _run_read_only(
        ["git", "-c", f"safe.directory={repository}", *args],
        cwd=repository,
        runner=runner,
    )


def _production_head(
    repository: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    proc = _git_read(repository, ["rev-parse", "HEAD"], runner=runner)
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _tracked_change_count(
    repository: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int | None:
    proc = _git_read(
        repository,
        ["status", "--porcelain=v1", "--untracked-files=no"],
        runner=runner,
    )
    if proc.returncode != 0:
        return None
    return sum(1 for line in proc.stdout.splitlines() if line.strip())


def _service_state(
    service: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    proc = _run_read_only(
        ["systemctl", "is-active", service],
        runner=runner,
    )
    value = proc.stdout.strip()
    return value if value else "unknown"


def _target_cursor(repository: Path, *, pool: str) -> str | None:
    path = repository / DETECTOR_STATE
    if path.is_symlink() or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    cursors = payload.get("cursors")
    if not isinstance(cursors, dict):
        return None
    value = cursors.get(pool)
    return str(value) if value else None


def _bundle_entries(bundle_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = bundle_report.get("entries")
    if not isinstance(entries, list):
        raise ValueError("private bundle entries are missing")
    by_path: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("private bundle entry is invalid")
        path = _safe_relative_path(str(entry.get("path", "")))
        if path in by_path:
            raise ValueError(f"private bundle path is duplicated: {path}")
        by_path[path] = entry
    if set(by_path) != {RESEARCH_STORE_PATH, STATE_READER_PATH}:
        raise ValueError("private bundle path scope is unexpected")
    return by_path


def _verify_private_bundle(
    *,
    bundle_module: Any,
    bundle_report: dict[str, Any],
) -> tuple[Path, dict[str, dict[str, Any]]]:
    bundle_module.validate_bundle_report(bundle_report)
    if bundle_report.get("bundle_ready") is not True:
        raise ValueError("private preserved-source bundle is not ready")

    bundle_dir = Path(str(bundle_report["bundle_dir"]))
    if bundle_dir.is_symlink():
        raise ValueError("private bundle directory must not be a symlink")
    resolved = bundle_dir.resolve(strict=True)
    var_tmp = Path("/var/tmp").resolve()
    if var_tmp not in resolved.parents:
        raise ValueError("private bundle must remain under /var/tmp")
    if not resolved.is_dir():
        raise ValueError("private bundle directory is missing")

    entries = _bundle_entries(bundle_report)
    for relative, entry in entries.items():
        target = resolved / relative
        if not _resolves_within(resolved, target):
            raise ValueError(f"private bundle path escapes root: {relative}")
        if _has_symlink_parent(resolved, target):
            raise ValueError(f"private bundle target has a symlinked parent: {relative}")
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"private bundle target is not a regular file: {relative}")
        if _git_blob_sha(target) != entry["preserved_candidate_blob"]:
            raise ValueError(f"private bundle candidate blob changed: {relative}")
        payload = target.read_bytes()
        if hashlib.sha256(payload).hexdigest() != entry["candidate_sha256"]:
            raise ValueError(f"private bundle candidate SHA-256 changed: {relative}")
        if len(payload) != entry["candidate_size"]:
            raise ValueError(f"private bundle candidate size changed: {relative}")
    return resolved, entries


def _classify_preserved_file(
    *,
    repository: Path,
    source_tree: Path,
    relative: str,
    expected_current_blob: str,
    target_blob: str,
) -> FileStatus:
    relative = _safe_relative_path(relative)
    source = source_tree / relative
    target = repository / relative

    source_blob: str | None = None
    current_blob: str | None = None

    if not _resolves_within(source_tree, source):
        status = "SOURCE_OUTSIDE_TREE"
    elif _has_symlink_parent(source_tree, source):
        status = "SOURCE_SYMLINK_PARENT"
    elif source.is_symlink():
        status = "SOURCE_SYMLINK"
    elif not source.exists():
        status = "SOURCE_MISSING"
    elif not source.is_file():
        status = "SOURCE_NOT_REGULAR_FILE"
    elif not _resolves_within(repository, target):
        status = "TARGET_OUTSIDE_REPOSITORY"
    elif _has_symlink_parent(repository, target):
        status = "CONFLICT_SYMLINK_PARENT"
    elif target.is_symlink():
        status = "CONFLICT_SYMLINK"
    elif target.exists() and not target.is_file():
        status = "CONFLICT_NON_FILE"
    else:
        source_blob = _git_blob_sha(source)
        current_blob = _git_blob_sha(target)
        if source_blob != target_blob:
            status = "SOURCE_MISMATCH"
        elif current_blob == target_blob:
            status = "ALREADY_TARGET"
        elif current_blob == expected_current_blob:
            status = "READY_UPDATE"
        elif current_blob is None:
            status = "CONFLICT_MISSING"
        else:
            status = "CONFLICT_MODIFIED"

    return FileStatus(
        path=relative,
        expected_current_blob=expected_current_blob,
        target_blob=target_blob,
        source_blob=source_blob,
        current_blob=current_blob,
        status=status,
    )


def _from_collection_file(item: Any) -> FileStatus:
    return FileStatus(
        path=str(item.path),
        expected_current_blob=item.expected_base_blob,
        target_blob=str(item.target_blob),
        source_blob=item.source_blob,
        current_blob=item.current_blob,
        status=str(item.status),
    )


def _layer_summary(files: list[FileStatus]) -> LayerSummary:
    if not files:
        raise ValueError("readiness layer cannot be empty")
    statuses = Counter(item.status for item in files)
    pending = tuple(
        {"path": item.path, "status": item.status}
        for item in files
        if item.status in {"READY_CREATE", "READY_UPDATE"}
    )
    nonready = tuple(
        {"path": item.path, "status": item.status}
        for item in files
        if item.status not in READY_STATUSES
    )
    return LayerSummary(
        content_ready=not nonready,
        deployed=all(item.status == "ALREADY_TARGET" for item in files),
        apply_authorized=False,
        files_changed=sum(
            item.status in {"READY_CREATE", "READY_UPDATE"}
            for item in files
        ),
        status_counts=dict(sorted(statuses.items())),
        pending=pending,
        nonready=nonready,
        files=tuple(files),
    )


def _state_summary(
    *,
    repository: Path,
    bundle: Path,
) -> StateReaderSummary:
    item = _classify_preserved_file(
        repository=repository,
        source_tree=bundle,
        relative=STATE_READER_PATH,
        expected_current_blob=EXPECTED_STATE_READER_CURRENT_BLOB,
        target_blob=EXPECTED_STATE_READER_CANDIDATE_BLOB,
    )
    return StateReaderSummary(
        content_ready=item.status in READY_STATUSES,
        deployed=item.status == "ALREADY_TARGET",
        status=item.status,
        expected_current_blob=EXPECTED_STATE_READER_CURRENT_BLOB,
        target_blob=EXPECTED_STATE_READER_CANDIDATE_BLOB,
        source_blob=item.source_blob,
        current_blob=item.current_blob,
    )


def _validate_layer_record(value: Any, *, label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"preserved readiness {label} must be an object")
    if set(value) != {
        "content_ready",
        "deployed",
        "apply_authorized",
        "files_changed",
        "status_counts",
        "pending",
        "nonready",
        "files",
    }:
        raise ValueError(f"preserved readiness {label} schema mismatch")

    files = value.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError(f"preserved readiness {label} files are invalid")

    statuses: list[str] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "expected_current_blob",
            "target_blob",
            "source_blob",
            "current_blob",
            "status",
        }:
            raise ValueError(f"preserved readiness {label} file schema mismatch")
        if not isinstance(item["path"], str) or not item["path"]:
            raise ValueError(f"preserved readiness {label} file path is invalid")
        if not _is_hex_digest(item["target_blob"], 40):
            raise ValueError(f"preserved readiness {label} target blob is invalid")
        for digest_field in (
            "expected_current_blob",
            "source_blob",
            "current_blob",
        ):
            digest = item[digest_field]
            if digest is not None and not _is_hex_digest(digest, 40):
                raise ValueError(
                    f"preserved readiness {label} {digest_field} is invalid"
                )
        status = item["status"]
        if not isinstance(status, str) or not status:
            raise ValueError(f"preserved readiness {label} status is invalid")
        statuses.append(status)

    expected_counts = dict(sorted(Counter(statuses).items()))
    if value.get("status_counts") != expected_counts:
        raise ValueError(f"preserved readiness {label} status counts mismatch")

    expected_pending = [
        {"path": item["path"], "status": item["status"]}
        for item in files
        if item["status"] in {"READY_CREATE", "READY_UPDATE"}
    ]
    expected_nonready = [
        {"path": item["path"], "status": item["status"]}
        for item in files
        if item["status"] not in READY_STATUSES
    ]
    if value.get("pending") != expected_pending:
        raise ValueError(f"preserved readiness {label} pending evidence mismatch")
    if value.get("nonready") != expected_nonready:
        raise ValueError(f"preserved readiness {label} nonready evidence mismatch")

    expected_content_ready = not expected_nonready
    expected_deployed = all(status == "ALREADY_TARGET" for status in statuses)
    expected_changed = sum(
        status in {"READY_CREATE", "READY_UPDATE"}
        for status in statuses
    )
    if value.get("content_ready") is not expected_content_ready:
        raise ValueError(f"preserved readiness {label} content-ready mismatch")
    if value.get("deployed") is not expected_deployed:
        raise ValueError(f"preserved readiness {label} deployed mismatch")
    if value.get("files_changed") != expected_changed:
        raise ValueError(f"preserved readiness {label} files-changed mismatch")
    if value.get("apply_authorized") is not False:
        raise ValueError(f"preserved readiness {label} must not authorize apply")


def _validate_state_record(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("preserved readiness state_reader must be an object")
    if set(value) != {
        "content_ready",
        "deployed",
        "status",
        "expected_current_blob",
        "target_blob",
        "source_blob",
        "current_blob",
    }:
        raise ValueError("preserved readiness state_reader schema mismatch")
    if value.get("expected_current_blob") != EXPECTED_STATE_READER_CURRENT_BLOB:
        raise ValueError("preserved readiness state-reader current binding mismatch")
    if value.get("target_blob") != EXPECTED_STATE_READER_CANDIDATE_BLOB:
        raise ValueError("preserved readiness state-reader target binding mismatch")

    for field in ("expected_current_blob", "target_blob"):
        if not _is_hex_digest(value.get(field), 40):
            raise ValueError(f"preserved readiness state-reader {field} is invalid")
    for field in ("source_blob", "current_blob"):
        digest = value.get(field)
        if digest is not None and not _is_hex_digest(digest, 40):
            raise ValueError(f"preserved readiness state-reader {field} is invalid")

    status = value.get("status")
    if not isinstance(status, str) or not status:
        raise ValueError("preserved readiness state-reader status is invalid")
    expected_content_ready = status in READY_STATUSES
    expected_deployed = status == "ALREADY_TARGET"
    if value.get("content_ready") is not expected_content_ready:
        raise ValueError("preserved readiness state-reader content-ready mismatch")
    if value.get("deployed") is not expected_deployed:
        raise ValueError("preserved readiness state-reader deployed mismatch")

    if status in READY_STATUSES and value.get("source_blob") != value["target_blob"]:
        raise ValueError("preserved readiness state-reader ready source mismatch")
    if status == "READY_UPDATE" and value.get("current_blob") != value[
        "expected_current_blob"
    ]:
        raise ValueError("preserved readiness state-reader update current mismatch")
    if status == "ALREADY_TARGET" and value.get("current_blob") != value[
        "target_blob"
    ]:
        raise ValueError("preserved readiness state-reader deployed current mismatch")


def validate_preserved_readiness(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("preserved readiness must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError("preserved readiness fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported preserved readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected preserved readiness artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("preserved readiness source lineage mismatch")
    if not _is_hex_digest(report.get("private_bundle_sha256"), 64):
        raise ValueError("preserved readiness bundle digest is invalid")
    if report.get("private_bundle_verified") is not True:
        raise ValueError("preserved readiness requires verified private bundle")

    production_head = report.get("production_head")
    if production_head is not None and not _is_hex_digest(production_head, 40):
        raise ValueError("preserved readiness production HEAD is invalid")
    expected_head_match = production_head == EXPECTED_PRODUCTION_HEAD
    if report.get("production_head_matches_reviewed_baseline") is not expected_head_match:
        raise ValueError("preserved readiness production-head flag mismatch")

    tracked = report.get("tracked_changes")
    if tracked is not None and (
        not isinstance(tracked, int)
        or isinstance(tracked, bool)
        or tracked < 0
    ):
        raise ValueError("preserved readiness tracked-change count is invalid")

    for field in (
        "detector_service",
        "watcher_service",
        "paper_service",
        "paper_timer",
        "target_pool",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"preserved readiness {field} is invalid")

    paper_account = report.get("paper_account")
    if paper_account is not None and (
        not isinstance(paper_account, str) or not paper_account
    ):
        raise ValueError("preserved readiness paper account is invalid")

    if report.get("target_pool") != TARGET_POOL:
        raise ValueError("preserved readiness target pool mismatch")

    _validate_layer_record(report.get("phase2"), label="phase2")
    _validate_state_record(report.get("state_reader"))
    _validate_layer_record(report.get("market_paper"), label="market_paper")

    expected_operational = (
        report["detector_service"] == "active"
        and report["watcher_service"] == "active"
    )
    if report.get("operational_services_healthy") is not expected_operational:
        raise ValueError("preserved readiness service-health flag mismatch")

    expected_manual_safe = (
        paper_account is not None
        and report["paper_service"] == "inactive"
        and report["paper_timer"] == "inactive"
    )
    if report.get("manual_mode_safe") is not expected_manual_safe:
        raise ValueError("preserved readiness manual-mode flag mismatch")

    expected_preflight = bool(
        report["private_bundle_verified"]
        and report["production_head_matches_reviewed_baseline"]
        and report["phase2"].get("content_ready") is True
        and report["state_reader"].get("content_ready") is True
        and report["market_paper"].get("content_ready") is True
    )
    if report.get("deployment_preflight_clean") is not expected_preflight:
        raise ValueError("preserved readiness preflight flag mismatch")

    expected_deployed = bool(
        report["phase2"].get("deployed") is True
        and report["state_reader"].get("deployed") is True
        and report["market_paper"].get("deployed") is True
    )
    if report.get("runtime_files_deployed") is not expected_deployed:
        raise ValueError("preserved readiness deployed flag mismatch")

    expected_runtime_ready = bool(
        expected_operational
        and expected_manual_safe
        and expected_deployed
    )
    if report.get("manual_paper_runtime_ready") is not expected_runtime_ready:
        raise ValueError("preserved readiness runtime-ready flag mismatch")

    for field in (
        "requires_fresh_handoff",
        "requires_preservation_aware_plan",
        "requires_separate_mutation_authorization",
    ):
        if report.get(field) is not True:
            raise ValueError(f"preserved readiness requires {field}=true")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if report.get(field) is not False:
            raise ValueError(f"preserved readiness requires {field}=false")

    if not _is_hex_digest(report.get("readiness_sha256"), 64):
        raise ValueError("preserved readiness digest is invalid")
    identity = {field: report[field] for field in REPORT_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["readiness_sha256"] != expected_digest:
        raise ValueError("preserved readiness digest mismatch")


def build_preserved_readiness(
    *,
    repository: str | Path,
    reviewed_source_tree: str | Path,
    private_bundle_report_path: str | Path,
    paper_account: str | None,
    pool: str = TARGET_POOL,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    repository_path = Path(repository).resolve()
    source = Path(reviewed_source_tree).resolve()
    if not (repository_path / ".git").exists():
        raise ValueError("production repository is not a Git tree")
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    _verify_reviewed_source(source)
    bundle_module = _load_module(
        source / PRESERVED_BUNDLE_TOOL,
        "manual_market_paper_preserved_readiness_bundle",
    )
    collection_module = _load_module(
        source / COLLECTION_TOOL,
        "manual_market_paper_preserved_readiness_collection",
    )

    bundle_report = _load_json(Path(private_bundle_report_path))
    bundle, bundle_entries = _verify_private_bundle(
        bundle_module=bundle_module,
        bundle_report=bundle_report,
    )

    if bundle_entries[RESEARCH_STORE_PATH]["preserved_candidate_blob"] != (
        EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB
    ):
        raise ValueError("private bundle research-store candidate mismatch")
    if bundle_entries[STATE_READER_PATH]["preserved_candidate_blob"] != (
        EXPECTED_STATE_READER_CANDIDATE_BLOB
    ):
        raise ValueError("private bundle state-reader candidate mismatch")

    phase2_raw = collection_module.preflight_collection_stack(
        repository=repository_path,
        source_tree=bundle,
        manifest=source / PHASE2_MANIFEST,
    )
    storage_items = [
        item for item in phase2_raw.files
        if item.path == STORAGE_PATH
    ]
    if len(storage_items) != 1:
        raise ValueError("Phase-2 storage prerequisite is missing from reviewed manifest")

    research_item = _classify_preserved_file(
        repository=repository_path,
        source_tree=bundle,
        relative=RESEARCH_STORE_PATH,
        expected_current_blob=EXPECTED_RESEARCH_STORE_CURRENT_BLOB,
        target_blob=EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
    )
    phase2 = _layer_summary(
        [research_item, _from_collection_file(storage_items[0])]
    )

    runtime_raw = collection_module.preflight_collection_stack(
        repository=repository_path,
        source_tree=bundle,
        manifest=source / RUNTIME_MANIFEST,
    )
    market_paper = _layer_summary(
        [_from_collection_file(item) for item in runtime_raw.files]
    )
    state_reader = _state_summary(
        repository=repository_path,
        bundle=bundle,
    )

    production_head = _production_head(repository_path, runner=runner)
    production_head_matches = production_head == EXPECTED_PRODUCTION_HEAD
    tracked_changes = _tracked_change_count(repository_path, runner=runner)

    detector_service = _service_state(DETECTOR_SERVICE, runner=runner)
    watcher_service = _service_state(WATCHER_SERVICE, runner=runner)
    operational_services_healthy = (
        detector_service == "active"
        and watcher_service == "active"
    )

    if paper_account is None:
        paper_service = "not-checked"
        paper_timer = "not-checked"
        manual_mode_safe = False
    else:
        paper_service = _service_state(
            PAPER_SERVICE_TEMPLATE.format(account=paper_account),
            runner=runner,
        )
        paper_timer = _service_state(
            PAPER_TIMER_TEMPLATE.format(account=paper_account),
            runner=runner,
        )
        manual_mode_safe = (
            paper_service == "inactive"
            and paper_timer == "inactive"
        )

    deployment_preflight_clean = bool(
        production_head_matches
        and phase2.content_ready
        and state_reader.content_ready
        and market_paper.content_ready
    )
    runtime_files_deployed = bool(
        phase2.deployed
        and state_reader.deployed
        and market_paper.deployed
    )
    manual_paper_runtime_ready = bool(
        operational_services_healthy
        and manual_mode_safe
        and runtime_files_deployed
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
        "reviewed_source_tree": str(source),
        "private_bundle_dir": str(bundle),
        "private_bundle_sha256": bundle_report["bundle_sha256"],
        "private_bundle_verified": True,
        "production_head": production_head,
        "production_head_matches_reviewed_baseline": production_head_matches,
        "tracked_changes": tracked_changes,
        "detector_service": detector_service,
        "watcher_service": watcher_service,
        "paper_account": paper_account,
        "paper_service": paper_service,
        "paper_timer": paper_timer,
        "manual_mode_safe": manual_mode_safe,
        "target_pool": pool,
        "target_pool_cursor": _target_cursor(repository_path, pool=pool),
        "phase2": phase2.to_record(),
        "state_reader": state_reader.to_record(),
        "market_paper": market_paper.to_record(),
        "deployment_preflight_clean": deployment_preflight_clean,
        "runtime_files_deployed": runtime_files_deployed,
        "operational_services_healthy": operational_services_healthy,
        "manual_paper_runtime_ready": manual_paper_runtime_ready,
        "requires_fresh_handoff": True,
        "requires_preservation_aware_plan": True,
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
        "readiness_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }
    validate_preserved_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only production readiness check for the privately preserved "
            "manual market/PAPER source bundle. Only research_store.py and "
            "state_reader.rs use preserved candidate targets; all unaffected "
            "files remain bound to the reviewed public manifests. This command "
            "never writes production files, restarts services, enables timers, "
            "or changes detector state."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--reviewed-source-tree", required=True)
    parser.add_argument("--private-bundle-report", required=True)
    parser.add_argument("--paper-account")
    parser.add_argument("--pool", default=TARGET_POOL)
    args = parser.parse_args()

    report = build_preserved_readiness(
        repository=args.repo,
        reviewed_source_tree=args.reviewed_source_tree,
        private_bundle_report_path=args.private_bundle_report,
        paper_account=args.paper_account,
        pool=args.pool,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["deployment_preflight_clean"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
