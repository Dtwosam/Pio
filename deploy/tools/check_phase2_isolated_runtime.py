#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any


PINNED_SOURCE_HEAD = "161b52788c6028d0f8fd05de847665c4add4e4a6"
EXECUTOR_RELATIVE = Path("rust-executor/target/release/meteora-executor")
WATCH_EXECUTOR_RELATIVE = Path("rust-executor/target/release/pio-phase2-account-watch")

TRACKED_CONTRACT = {
    "python-learner/src/meteora_learner/calibration_queue.py":
        "d0c5721cac805dbd80ecacef21f81ac654185212",
    "python-learner/src/meteora_learner/composition_prestate.py":
        "85ab78d188fec772278771bfcb1091cab8c3304c",
    "python-learner/src/meteora_learner/research_store.py":
        "d3ffb8815e6efa949b6bf7a6f33ba60099f68357",
    "python-learner/src/meteora_learner/phase2_evidence_cycle.py":
        "cd66398be75698aed032adb15f5e1a7739b0df72",
    "python-learner/src/meteora_learner/phase2_position_observation.py":
        "71855a97c0dfe1f7c8db5df6c336e99584623a7c",
    "python-learner/src/meteora_learner/phase2_calibration_reinspection.py":
        "189fc19d7b77d3bad5d8a1739378b223f6abcb1d",
    "python-learner/src/meteora_learner/phase2_prestate_verification_runner.py":
        "4686b2fc8da6b97225de7cfb248155f163a287c5",
    "python-learner/src/meteora_learner/phase2_rpc_guard.py":
        "36277db7d701e34bb765b5be97b0ad757d784899",
    "python-learner/src/meteora_learner/phase2_event_prestate.py":
        "c06e4e20b851ffe21730723cf873a623d1710d70",
    "python-learner/src/meteora_learner/phase2_stream_supervisor.py":
        "53678b7b33c73ac81fa476a8cf99fa6d149e5d77",
    "scripts/phase2-add-detector.py":
        "a7a6a888e7833fab73ffd564b1ec40195b185f33",
    "deploy/tools/autopause_phase2_isolated_timer.py":
        "a2b1feeef9a5abd7cb1340a34bce97900b156105",
    "deploy/systemd/pio-phase2-isolated-prestate-stream@.service":
        "4270a0c7b3aec8844ae9f06c6a073aa7b9d247af",
    "deploy/systemd/pio-phase2-isolated-add-detector.service":
        "fdb5334cca2174421e084813b42052f5399c435b",
    "deploy/systemd/pio-phase2-isolated-evidence-cycle.service":
        "3a82fc92e9785f6dcbde8cfd9c3458a71e34c8e3",
    "deploy/systemd/pio-phase2-isolated-evidence-cycle.timer":
        "e6781b6e7f4d235ec170d7f08d8f9c25414100ef",
    "deploy/systemd/pio-phase2-isolated-rate-limit-pause.service":
        "07e8d4a58243537bea74226da4ab6548bf212496",
    "rust-executor/src/bin/pio-phase2-account-watch.rs":
        "29ebea566a9ed4599b6dbe1a486a2ce509412995",
    "rust-executor/src/main.rs":
        "96ecb4479482d146abbecafc53466b93fa452d80",
    "rust-executor/src/state_reader.rs":
        "30d1435af1329bca07f73d6639539b43503e84e9",
}

EXPECTED_DIRTY_PATHS = {
    "python-learner/src/meteora_learner/calibration_queue.py",
    "python-learner/src/meteora_learner/composition_prestate.py",
    "python-learner/src/meteora_learner/research_store.py",
}


@dataclass(frozen=True)
class RuntimeFile:
    path: str
    expected_blob: str
    current_blob: str | None
    matches: bool


@dataclass(frozen=True)
class IsolatedRuntimeReport:
    source_tree: str
    source_head: str | None
    source_head_matches: bool
    dirty_paths: tuple[str, ...]
    dirty_paths_match_expected: bool
    files: tuple[RuntimeFile, ...]
    executor_path: str
    executor_exists: bool
    executor_executable: bool
    watch_executor_path: str
    watch_executor_exists: bool
    watch_executor_executable: bool
    runtime_ready: bool
    production_tree_modified: bool
    rpc_called: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _git_blob_sha(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _run_git(source: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(source),
        text=True,
        capture_output=True,
        check=False,
    )


def _head(source: Path) -> str | None:
    proc = _run_git(source, "rev-parse", "HEAD")
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _dirty_paths(source: Path) -> tuple[str, ...]:
    proc = _run_git(
        source,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
    )
    if proc.returncode != 0:
        raise ValueError("cannot inspect isolated runtime Git status")
    paths = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        raw = line[3:].strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        paths.append(raw)
    return tuple(sorted(set(paths)))


def inspect_runtime(source_tree: str | Path) -> IsolatedRuntimeReport:
    source = Path(source_tree).resolve()
    if source == Path("/opt/pio").resolve():
        raise ValueError("isolated runtime source must not be /opt/pio")
    if not source.is_dir() or not (source / ".git").exists():
        raise ValueError(f"isolated runtime Git tree is missing: {source}")

    source_head = _head(source)
    dirty = _dirty_paths(source)
    dirty_ok = set(dirty) == EXPECTED_DIRTY_PATHS

    files = tuple(
        RuntimeFile(
            path=relative,
            expected_blob=expected,
            current_blob=_git_blob_sha(source / relative),
            matches=_git_blob_sha(source / relative) == expected,
        )
        for relative, expected in sorted(TRACKED_CONTRACT.items())
    )

    executor = source / EXECUTOR_RELATIVE
    exists = executor.is_file() and not executor.is_symlink()
    executable = exists and os.access(executor, os.X_OK)

    watch_executor = source / WATCH_EXECUTOR_RELATIVE
    watch_exists = (
        watch_executor.is_file()
        and not watch_executor.is_symlink()
    )
    watch_executable = (
        watch_exists
        and os.access(watch_executor, os.X_OK)
    )

    ready = bool(
        source_head == PINNED_SOURCE_HEAD
        and dirty_ok
        and all(item.matches for item in files)
        and executable
        and watch_executable
    )

    return IsolatedRuntimeReport(
        source_tree=str(source),
        source_head=source_head,
        source_head_matches=source_head == PINNED_SOURCE_HEAD,
        dirty_paths=dirty,
        dirty_paths_match_expected=dirty_ok,
        files=files,
        executor_path=str(executor),
        executor_exists=exists,
        executor_executable=executable,
        watch_executor_path=str(watch_executor),
        watch_executor_exists=watch_exists,
        watch_executor_executable=watch_executable,
        runtime_ready=ready,
        production_tree_modified=False,
        rpc_called=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a pinned side-built Phase-2 runtime without touching "
            "the production checkout or making RPC calls."
        )
    )
    parser.add_argument("--source-tree", required=True)
    args = parser.parse_args()

    report = inspect_runtime(args.source_tree)
    print(json.dumps(report.to_record(), indent=2))
    if not report.runtime_ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
