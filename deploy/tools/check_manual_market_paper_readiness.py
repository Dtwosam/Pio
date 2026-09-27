from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


TARGET_POOL = "54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ"
DETECTOR_SERVICE = "pio-phase2-add-detector.service"
WATCHER_SERVICE = "pio-phase2-prestate-watch.service"
PAPER_SERVICE_TEMPLATE = "pio-paper@{account}.service"
PAPER_TIMER_TEMPLATE = "pio-paper@{account}.timer"
DETECTOR_STATE = Path("data/phase2-add-detector-state.json")

PHASE2_PREREQUISITES_MANIFEST = Path("deploy/manifests/market-paper-phase2-prerequisites.json")
MARKET_PAPER_MANIFEST = Path("deploy/manifests/market-paper-manual-cycle.json")
COLLECTION_TOOL = Path("deploy/tools/apply_phase2_collection_stack.py")
STATE_READER_TOOL = Path("deploy/tools/apply_phase2_single_slot_stack_patch.py")
STATE_READER_PATCH = Path(
    "deploy/patches/phase2-single-slot-stack-state-reader.patch"
)


REVIEWED_SOURCE_BLOBS = {
    COLLECTION_TOOL: "a8a76cd4db95867a842de93f8688daa0cd7100c3",
    STATE_READER_TOOL: "a2e4e0c0435f15be7126e253d1861955be6c2ecd",
    PHASE2_PREREQUISITES_MANIFEST: "dfb1eb62241f6fc3b6351e3b40b524fb68f4a61e",
    MARKET_PAPER_MANIFEST: "b824aae4d37df77074f58753eed29ebe2013e659",
    STATE_READER_PATCH: "330e2c33956f8a96850a1e072d6a2fa0a4d619af",
}


@dataclass(frozen=True)
class OverlaySummary:
    content_ready: bool
    deployed: bool
    apply_authorized: bool
    files_changed: int
    status_counts: dict[str, int]
    pending: tuple[dict[str, str], ...]
    nonready: tuple[dict[str, str], ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProjectedOverlaySummary:
    content_ready: bool
    files_changed: int
    status_counts: dict[str, int]
    pending: tuple[dict[str, str], ...]
    nonready: tuple[dict[str, str], ...]
    projected_prerequisite_paths: tuple[str, ...]
    mutation_authorized: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StateReaderSummary:
    preflight_ready: bool
    deployed: bool
    status: str
    error_category: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionReadinessReport:
    repository: str
    source_tree: str
    production_head: str | None
    tracked_changes: int | None
    detector_service: str
    watcher_service: str
    paper_account: str | None
    paper_service: str
    paper_timer: str
    manual_mode_safe: bool
    target_pool: str
    target_pool_cursor: str | None
    phase2: OverlaySummary
    state_reader: StateReaderSummary
    market_paper: OverlaySummary
    market_paper_after_prerequisites: ProjectedOverlaySummary
    deployment_preflight_clean: bool
    runtime_files_deployed: bool
    operational_services_healthy: bool
    manual_paper_runtime_ready: bool
    mutation_authorized: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _verify_reviewed_source_artifacts(source: Path) -> None:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if not path.is_file():
            raise ValueError(f"reviewed source artifact is missing: {relative}")
        actual = _git_blob_sha(path)
        if actual != expected_blob:
            raise ValueError(
                f"reviewed source artifact mismatch: {relative}"
            )


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load tool module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


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


def _manual_mode_safe(
    *,
    paper_service: str,
    paper_timer: str,
) -> bool:
    return paper_service == "inactive" and paper_timer == "inactive"


def _git_head(
    repo: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    proc = _run_read_only(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        runner=runner,
    )
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _tracked_change_count(
    repo: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int | None:
    proc = _run_read_only(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=repo,
        runner=runner,
    )
    if proc.returncode != 0:
        return None
    return sum(1 for line in proc.stdout.splitlines() if line.strip())


def _target_cursor(repo: Path, *, pool: str) -> str | None:
    state_path = repo / DETECTOR_STATE
    if not state_path.is_file():
        return None
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    cursors = payload.get("cursors")
    if not isinstance(cursors, dict):
        return None
    value = cursors.get(pool)
    return str(value) if value else None


def _overlay_summary(report: Any) -> OverlaySummary:
    statuses = Counter(item.status for item in report.files)
    pending = tuple(
        {"path": item.path, "status": item.status}
        for item in report.files
        if item.status in {"READY_CREATE", "READY_UPDATE"}
    )
    nonready = tuple(
        {"path": item.path, "status": item.status}
        for item in report.files
        if item.status not in {
            "ALREADY_TARGET",
            "READY_CREATE",
            "READY_UPDATE",
        }
    )
    deployed = bool(report.files) and all(
        item.status == "ALREADY_TARGET"
        for item in report.files
    )
    return OverlaySummary(
        content_ready=bool(report.content_ready),
        deployed=deployed,
        apply_authorized=bool(report.apply_authorized),
        files_changed=int(report.files_changed),
        status_counts=dict(sorted(statuses.items())),
        pending=pending,
        nonready=nonready,
    )


def _projected_market_overlay_summary(
    market_report: Any,
    prerequisite_report: Any,
) -> ProjectedOverlaySummary:
    ready_statuses = {"ALREADY_TARGET", "READY_CREATE", "READY_UPDATE"}
    structural_failures = {
        "SOURCE_OUTSIDE_TREE",
        "SOURCE_SYMLINK",
        "SOURCE_MISSING",
        "SOURCE_NOT_REGULAR_FILE",
        "TARGET_OUTSIDE_REPOSITORY",
        "CONFLICT_SYMLINK",
        "CONFLICT_NON_FILE",
        "SOURCE_MISMATCH",
    }

    projected_blobs = {
        item.path: item.target_blob
        for item in prerequisite_report.files
        if item.status in ready_statuses
    }

    projected: list[tuple[str, str]] = []
    projected_paths: list[str] = []
    for item in market_report.files:
        if item.status in structural_failures:
            status = item.status
        else:
            current_blob = item.current_blob
            if item.path in projected_blobs:
                current_blob = projected_blobs[item.path]
                if current_blob != item.current_blob:
                    projected_paths.append(item.path)

            if item.source_blob != item.target_blob:
                status = "SOURCE_MISMATCH"
            elif current_blob == item.target_blob:
                status = "ALREADY_TARGET"
            elif current_blob is None and item.expected_base_blob is None:
                status = "READY_CREATE"
            elif current_blob == item.expected_base_blob:
                status = "READY_UPDATE"
            elif current_blob is None:
                status = "CONFLICT_MISSING"
            elif item.expected_base_blob is None:
                status = "CONFLICT_UNEXPECTED_EXISTING"
            else:
                status = "CONFLICT_MODIFIED"

        projected.append((item.path, status))

    statuses = Counter(status for _, status in projected)
    pending = tuple(
        {"path": path, "status": status}
        for path, status in projected
        if status in {"READY_CREATE", "READY_UPDATE"}
    )
    nonready = tuple(
        {"path": path, "status": status}
        for path, status in projected
        if status not in ready_statuses
    )
    return ProjectedOverlaySummary(
        content_ready=all(status in ready_statuses for _, status in projected),
        files_changed=sum(
            status in {"READY_CREATE", "READY_UPDATE"}
            for _, status in projected
        ),
        status_counts=dict(sorted(statuses.items())),
        pending=pending,
        nonready=nonready,
        projected_prerequisite_paths=tuple(sorted(set(projected_paths))),
        mutation_authorized=False,
    )


def build_production_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    pool: str = TARGET_POOL,
    paper_account: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ProductionReadinessReport:
    repo = Path(repository).resolve()
    source = Path(source_tree).resolve()
    if not (repo / ".git").exists():
        raise ValueError(f"production repository is not a git tree: {repo}")
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")
    _verify_reviewed_source_artifacts(source)

    collection_module = _load_module(
        source / COLLECTION_TOOL,
        "market_paper_collection_preflight",
    )
    state_module = _load_module(
        source / STATE_READER_TOOL,
        "market_paper_state_reader_preflight",
    )

    phase2_report = collection_module.preflight_collection_stack(
        repository=repo,
        source_tree=source,
        manifest=source / PHASE2_PREREQUISITES_MANIFEST,
    )
    phase2 = _overlay_summary(phase2_report)

    market_report = collection_module.preflight_collection_stack(
        repository=repo,
        source_tree=source,
        manifest=source / MARKET_PAPER_MANIFEST,
    )
    market_paper = _overlay_summary(market_report)
    market_paper_after_prerequisites = _projected_market_overlay_summary(
        market_report,
        phase2_report,
    )

    try:
        state_result = state_module.apply_guarded_patch(
            repository=repo,
            patch=source / STATE_READER_PATCH,
            reference_patch=source / STATE_READER_PATCH,
            apply=False,
        )
        state_reader = StateReaderSummary(
            preflight_ready=bool(state_result.ready),
            deployed=(state_result.status == "ALREADY_TARGET"),
            status=str(state_result.status),
            error_category=None,
        )
    except Exception:
        state_reader = StateReaderSummary(
            preflight_ready=False,
            deployed=False,
            status="CONFLICT",
            error_category="STATE_READER_PREFLIGHT_FAILED",
        )

    detector_service = _service_state(DETECTOR_SERVICE, runner=runner)
    watcher_service = _service_state(WATCHER_SERVICE, runner=runner)
    service_health = (
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
        manual_mode_safe = _manual_mode_safe(
            paper_service=paper_service,
            paper_timer=paper_timer,
        )

    deployment_preflight_clean = (
        phase2.content_ready
        and state_reader.preflight_ready
        and market_paper_after_prerequisites.content_ready
    )
    runtime_files_deployed = (
        phase2.deployed
        and state_reader.deployed
        and market_paper.deployed
    )
    runtime_ready = (
        service_health
        and runtime_files_deployed
        and manual_mode_safe
    )

    return ProductionReadinessReport(
        repository=str(repo),
        source_tree=str(source),
        production_head=_git_head(repo, runner=runner),
        tracked_changes=_tracked_change_count(repo, runner=runner),
        detector_service=detector_service,
        watcher_service=watcher_service,
        paper_account=paper_account,
        paper_service=paper_service,
        paper_timer=paper_timer,
        manual_mode_safe=manual_mode_safe,
        target_pool=pool,
        target_pool_cursor=_target_cursor(repo, pool=pool),
        phase2=phase2,
        state_reader=state_reader,
        market_paper=market_paper,
        market_paper_after_prerequisites=market_paper_after_prerequisites,
        deployment_preflight_clean=deployment_preflight_clean,
        runtime_files_deployed=runtime_files_deployed,
        operational_services_healthy=service_health,
        manual_paper_runtime_ready=runtime_ready,
        mutation_authorized=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only production readiness check for the manual market/PAPER "
            "runtime. This command never applies source changes, restarts "
            "services, enables timers, or writes detector state."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--pool", default=TARGET_POOL)
    parser.add_argument(
        "--paper-account",
        help=(
            "Paper account name whose systemd service/timer must be inactive "
            "before manual PAPER readiness can be true."
        ),
    )
    args = parser.parse_args()

    report = build_production_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        pool=args.pool,
        paper_account=args.paper_account,
    )
    print(json.dumps(report.to_record(), indent=2, sort_keys=True))

    if not report.deployment_preflight_clean:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
