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
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PREFLIGHT_HANDOFF_V1"
READINESS_TOOL = Path("deploy/tools/check_manual_market_paper_readiness.py")
EXPECTED_READINESS_TOOL_BLOB = "d685c45492397fb921d9524abdd3757e8f7d7fab"

IDENTITY_FIELDS = (
    "repository",
    "production_head",
    "tracked_changes",
    "tracked_diff_sha256",
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
    "mutation_authorized",
)


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _load_readiness_module(source_tree: Path) -> Any:
    tool = source_tree / READINESS_TOOL
    if not tool.is_file():
        raise ValueError(f"reviewed readiness tool is missing: {READINESS_TOOL}")
    if _git_blob_sha(tool) != EXPECTED_READINESS_TOOL_BLOB:
        raise ValueError("reviewed readiness tool blob mismatch")

    spec = importlib.util.spec_from_file_location(
        "manual_market_paper_readiness_handoff_source",
        tool,
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load readiness tool: {tool}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _tracked_diff_sha256(
    repository: str | Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> str:
    repo = Path(repository).resolve()
    proc = runner(
        [
            "git",
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        ],
        cwd=str(repo),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError("cannot fingerprint tracked production diff")
    stdout = proc.stdout
    if not isinstance(stdout, bytes):
        stdout = str(stdout).encode("utf-8")
    return hashlib.sha256(stdout).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _state_identity(report: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in IDENTITY_FIELDS if field not in report]
    if missing:
        raise ValueError(
            "readiness report is missing identity fields: "
            + ", ".join(sorted(missing))
        )
    return {field: report[field] for field in IDENTITY_FIELDS}


def _state_digest(identity: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(identity)).hexdigest()


def _handoff_ready(identity: dict[str, Any]) -> bool:
    return (
        bool(identity["paper_account"])
        and identity["deployment_preflight_clean"] is True
        and identity["operational_services_healthy"] is True
        and identity["manual_mode_safe"] is True
        and identity["mutation_authorized"] is False
    )


def build_handoff_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    identity = _state_identity(report)
    ready = _handoff_ready(identity)
    return {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "readiness_tool_blob": EXPECTED_READINESS_TOOL_BLOB,
        "production_state_sha256": _state_digest(identity),
        "handoff_ready": ready,
        "deployment_needed": not bool(identity["runtime_files_deployed"]),
        "mutation_authorized": False,
        "state": identity,
    }


def validate_handoff_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported handoff snapshot format")
    if snapshot.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected handoff snapshot type")
    if snapshot.get("readiness_tool_blob") != EXPECTED_READINESS_TOOL_BLOB:
        raise ValueError("handoff snapshot readiness-tool lineage mismatch")
    if snapshot.get("mutation_authorized") is not False:
        raise ValueError("handoff snapshot must never authorize mutation")

    state = snapshot.get("state")
    if not isinstance(state, dict):
        raise ValueError("handoff snapshot state is missing")
    identity = _state_identity(state)

    expected_digest = _state_digest(identity)
    if snapshot.get("production_state_sha256") != expected_digest:
        raise ValueError("handoff snapshot state digest mismatch")
    if snapshot.get("handoff_ready") is not _handoff_ready(identity):
        raise ValueError("handoff snapshot readiness flag mismatch")
    deployment_needed = not bool(identity["runtime_files_deployed"])
    if snapshot.get("deployment_needed") is not deployment_needed:
        raise ValueError("handoff snapshot deployment-needed flag mismatch")


def compare_handoff_snapshot(
    snapshot: dict[str, Any],
    current_report: dict[str, Any],
) -> dict[str, Any]:
    validate_handoff_snapshot(snapshot)
    current = build_handoff_snapshot(current_report)
    prior_state = snapshot["state"]
    current_state = current["state"]
    changed = tuple(
        field
        for field in IDENTITY_FIELDS
        if prior_state[field] != current_state[field]
    )
    matches = (
        snapshot["production_state_sha256"]
        == current["production_state_sha256"]
    )
    return {
        "format_version": FORMAT_VERSION,
        "artifact_type": "MANUAL_MARKET_PAPER_PREFLIGHT_VERIFY_V1",
        "snapshot_handoff_ready": bool(snapshot["handoff_ready"]),
        "current_handoff_ready": bool(current["handoff_ready"]),
        "state_matches": matches,
        "changed_sections": changed,
        "snapshot_state_sha256": snapshot["production_state_sha256"],
        "current_state_sha256": current["production_state_sha256"],
        "mutation_authorized": False,
    }


def _build_current_report(
    *,
    repository: str,
    source_tree: Path,
    pool: str | None,
    paper_account: str,
) -> dict[str, Any]:
    repo = Path(repository).resolve()
    diff_before = _tracked_diff_sha256(repo)

    module = _load_readiness_module(source_tree)
    kwargs: dict[str, Any] = {
        "repository": repo,
        "source_tree": source_tree,
        "paper_account": paper_account,
    }
    if pool is not None:
        kwargs["pool"] = pool
    report = module.build_production_readiness(**kwargs)
    record = report.to_record()

    diff_after = _tracked_diff_sha256(repo)
    if diff_before != diff_after:
        raise ValueError(
            "tracked production diff changed during read-only preflight"
        )
    record["tracked_diff_sha256"] = diff_after
    return record


def _load_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("handoff snapshot must be a JSON object")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Capture or verify a deterministic read-only handoff for the "
            "manual market/PAPER production preflight. This tool never "
            "applies source changes, restarts services, enables timers, "
            "moves detector cursors, or authorizes mutation."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--repo", default="/opt/pio")
        subparser.add_argument("--source-tree", required=True)
        subparser.add_argument("--paper-account", required=True)
        subparser.add_argument("--pool")

    capture = subparsers.add_parser(
        "capture",
        help="Print a sealed preflight snapshot to stdout.",
    )
    add_common(capture)

    verify = subparsers.add_parser(
        "verify",
        help="Re-run preflight and compare it with a saved snapshot.",
    )
    add_common(verify)
    verify.add_argument("--snapshot", required=True)

    args = parser.parse_args()
    source_tree = Path(args.source_tree).resolve()
    current_report = _build_current_report(
        repository=args.repo,
        source_tree=source_tree,
        pool=args.pool,
        paper_account=args.paper_account,
    )

    if args.command == "capture":
        result = build_handoff_snapshot(current_report)
        print(json.dumps(result, indent=2, sort_keys=True))
        if not result["handoff_ready"]:
            raise SystemExit(2)
        return

    snapshot = _load_snapshot(Path(args.snapshot))
    result = compare_handoff_snapshot(snapshot, current_report)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not (
        result["snapshot_handoff_ready"]
        and result["current_handoff_ready"]
        and result["state_matches"]
    ):
        raise SystemExit(3)


if __name__ == "__main__":
    main()
