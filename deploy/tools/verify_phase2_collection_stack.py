#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class VerifiedCollectionFile:
    path: str
    git_blob_sha: str


@dataclass(frozen=True)
class Phase2CollectionStackVerification:
    repository: str
    manifest: str
    format_version: int
    components_verified: int
    critical_files_verified: int
    production_deployment_authorized: bool
    detector_cursor_movement_authorized: bool
    service_restart_authorized: bool
    verified: bool
    files: tuple[VerifiedCollectionFile, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def verify_phase2_collection_stack(
    *,
    repository: str | Path,
    manifest_path: str | Path | None = None,
) -> Phase2CollectionStackVerification:
    root = Path(repository).resolve()
    if not root.is_dir():
        raise ValueError(f"repository directory does not exist: {root}")

    manifest = (
        Path(manifest_path).resolve()
        if manifest_path is not None
        else root / "deploy" / "manifests" / "phase2-collection-integration.json"
    )
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError("collection manifest is missing or is a symlink")

    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("collection manifest is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("collection manifest must be a JSON object")
    if int(payload.get("format_version", -1)) != 1:
        raise ValueError("unsupported collection manifest format")

    for key in (
        "production_deployment_authorized",
        "detector_cursor_movement_authorized",
        "service_restart_authorized",
    ):
        if payload.get(key) is not False:
            raise ValueError(f"{key} must remain false")

    components = payload.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("collection manifest has no components")
    seen_prs: set[int] = set()
    for item in components:
        if not isinstance(item, dict):
            raise ValueError("collection component must be an object")
        pr = item.get("pr")
        if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
            raise ValueError("collection component PR must be a positive integer")
        if pr in seen_prs:
            raise ValueError(f"duplicate collection component PR: {pr}")
        seen_prs.add(pr)
        head = str(item.get("head", ""))
        if not SHA_RE.fullmatch(head):
            raise ValueError(f"invalid component head for PR #{pr}")
        role = str(item.get("role", "")).strip()
        if not role:
            raise ValueError(f"missing component role for PR #{pr}")

    critical = payload.get("critical_file_blobs")
    if not isinstance(critical, dict) or not critical:
        raise ValueError("collection manifest has no critical file blobs")

    verified_files: list[VerifiedCollectionFile] = []
    for relative, expected in sorted(critical.items()):
        if not isinstance(relative, str) or not relative:
            raise ValueError("critical file path is invalid")
        rel = Path(relative)
        if (
            rel.is_absolute()
            or ".." in rel.parts
            or relative in {".", ".."}
        ):
            raise ValueError(f"unsafe critical file path: {relative}")
        expected_sha = str(expected)
        if not SHA_RE.fullmatch(expected_sha):
            raise ValueError(
                f"invalid critical file Git blob SHA: {relative}"
            )

        path = root / rel
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"critical file missing or not regular: {relative}"
            )
        actual = _git_blob_sha(path)
        if actual != expected_sha:
            raise ValueError(
                f"critical file blob mismatch: {relative}"
            )
        verified_files.append(
            VerifiedCollectionFile(
                path=relative,
                git_blob_sha=actual,
            )
        )

    state_reader = str(payload.get("state_reader_target_blob", ""))
    expected_state_reader = critical.get(
        "rust-executor/src/state_reader.rs"
    )
    if (
        not SHA_RE.fullmatch(state_reader)
        or state_reader != expected_state_reader
    ):
        raise ValueError(
            "state_reader_target_blob does not match critical file lineage"
        )

    return Phase2CollectionStackVerification(
        repository=str(root),
        manifest=str(manifest),
        format_version=1,
        components_verified=len(components),
        critical_files_verified=len(verified_files),
        production_deployment_authorized=False,
        detector_cursor_movement_authorized=False,
        service_restart_authorized=False,
        verified=True,
        files=tuple(verified_files),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the checked-out Phase-2 read-only collection stack "
            "against its pinned integration manifest. This command is read-only."
        )
    )
    parser.add_argument(
        "--repository",
        default=str(Path(__file__).resolve().parents[2]),
    )
    parser.add_argument("--manifest")
    args = parser.parse_args()

    result = verify_phase2_collection_stack(
        repository=args.repository,
        manifest_path=args.manifest,
    )
    print(json.dumps(result.to_record(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
