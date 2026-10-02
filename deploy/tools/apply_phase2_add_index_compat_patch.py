#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


PATCH_PATH = (
    Path(__file__).resolve().parents[1]
    / "patches"
    / "phase2-production-add-index-compat.patch"
)
EXPECTED_PATCH_SHA256 = "cd9144a4bb001424ad8503a23329b14e72c119860619e4d4c6251004a1f094a3"

FILE_CONTRACT = {
    "python-learner/src/meteora_learner/calibration_queue.py": (
        "d22786a5d4f40c993b1f818aecd2cc777820c832",
        "d0c5721cac805dbd80ecacef21f81ac654185212",
    ),
    "python-learner/src/meteora_learner/composition_prestate.py": (
        "feb13b8ea90af0875f85f6a4b955ee145193d4e4",
        "85ab78d188fec772278771bfcb1091cab8c3304c",
    ),
    "python-learner/src/meteora_learner/research_store.py": (
        "c9b9de5838d95a86bddffa6166b4d7a62e91cf16",
        "d3ffb8815e6efa949b6bf7a6f33ba60099f68357",
    ),
}


@dataclass(frozen=True)
class CompatFile:
    path: str
    expected_base_blob: str
    expected_target_blob: str
    current_blob: str | None
    status: str


@dataclass(frozen=True)
class CompatPatchReport:
    source_tree: str
    patch: str
    patch_sha256: str
    status: str
    ready: bool
    applied: bool
    files: tuple[CompatFile, ...]
    production_tree_modified: bool
    service_control_performed: bool
    rpc_called: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def git_blob_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_tree(value: str | Path) -> Path:
    source = Path(value).resolve()
    if not source.is_dir():
        raise ValueError(f"source tree is missing: {source}")
    if source == Path("/opt/pio").resolve():
        raise ValueError("compatibility patch must not be applied directly to /opt/pio")
    return source


def _patch() -> Path:
    patch = PATCH_PATH.resolve()
    if patch.is_symlink() or not patch.is_file():
        raise ValueError(f"reviewed compatibility patch is missing: {patch}")
    if sha256(patch) != EXPECTED_PATCH_SHA256:
        raise ValueError("reviewed compatibility patch bytes do not match pinned SHA256")
    return patch


def _inspect(source: Path) -> tuple[CompatFile, ...]:
    rows = []
    for relative, (base_blob, target_blob) in FILE_CONTRACT.items():
        path = source / relative
        if path.is_symlink():
            status = "SOURCE_SYMLINK"
            current = None
        elif not path.is_file():
            status = "SOURCE_MISSING"
            current = None
        else:
            current = git_blob_sha(path)
            if current == target_blob:
                status = "ALREADY_TARGET"
            elif current == base_blob:
                status = "READY_APPLY"
            else:
                status = "SOURCE_DRIFT"
        rows.append(
            CompatFile(
                path=relative,
                expected_base_blob=base_blob,
                expected_target_blob=target_blob,
                current_blob=current,
                status=status,
            )
        )
    return tuple(rows)


def _git_apply(source: Path, patch: Path, *, check: bool) -> None:
    command = ["git", "apply"]
    if check:
        command.append("--check")
    command.extend(["--whitespace=error-all", str(patch)])
    proc = subprocess.run(
        command,
        cwd=str(source),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        mode = "preflight" if check else "apply"
        raise ValueError(f"compatibility patch {mode} failed")


def evaluate(*, source_tree: str | Path, apply: bool = False) -> CompatPatchReport:
    source = _source_tree(source_tree)
    patch = _patch()
    files = _inspect(source)
    statuses = {item.status for item in files}

    if statuses == {"ALREADY_TARGET"}:
        return CompatPatchReport(
            source_tree=str(source),
            patch=str(patch),
            patch_sha256=EXPECTED_PATCH_SHA256,
            status="ALREADY_TARGET",
            ready=True,
            applied=apply,
            files=files,
            production_tree_modified=False,
            service_control_performed=False,
            rpc_called=False,
        )

    if statuses != {"READY_APPLY"}:
        return CompatPatchReport(
            source_tree=str(source),
            patch=str(patch),
            patch_sha256=EXPECTED_PATCH_SHA256,
            status="SOURCE_DRIFT",
            ready=False,
            applied=False,
            files=files,
            production_tree_modified=False,
            service_control_performed=False,
            rpc_called=False,
        )

    _git_apply(source, patch, check=True)
    if not apply:
        return CompatPatchReport(
            source_tree=str(source),
            patch=str(patch),
            patch_sha256=EXPECTED_PATCH_SHA256,
            status="READY_APPLY",
            ready=True,
            applied=False,
            files=files,
            production_tree_modified=False,
            service_control_performed=False,
            rpc_called=False,
        )

    _git_apply(source, patch, check=False)
    updated = _inspect(source)
    if {item.status for item in updated} != {"ALREADY_TARGET"}:
        raise ValueError("post-apply compatibility blob verification failed")
    return CompatPatchReport(
        source_tree=str(source),
        patch=str(patch),
        patch_sha256=EXPECTED_PATCH_SHA256,
        status="APPLIED",
        ready=True,
        applied=True,
        files=updated,
        production_tree_modified=False,
        service_control_performed=False,
        rpc_called=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the production-derived Phase-2 add-index compatibility "
            "patch only to an isolated reviewed source tree."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = evaluate(source_tree=args.source_tree, apply=args.apply)
    print(json.dumps(report.to_record(), indent=2))
    if not report.ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
