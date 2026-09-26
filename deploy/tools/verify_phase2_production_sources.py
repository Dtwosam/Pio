#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable


LABEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class VerifiedSource:
    label: str
    captured_path: str
    sha256: str
    imported_path: str | None


@dataclass(frozen=True)
class ProductionSourceVerification:
    snapshot_directory: str
    format_version: int
    sources_verified: int
    imports_verified: int
    verified: bool
    sources: tuple[VerifiedSource, ...]

    def to_record(self) -> dict[str, object]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_import_spec(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise ValueError(
            "repo source must use LABEL=/absolute/path form"
        )
    label, raw_path = value.split("=", 1)
    label = label.strip()
    raw_path = raw_path.strip()
    if not label or not LABEL_RE.fullmatch(label):
        raise ValueError(
            "repo source label may contain only letters, numbers, "
            "dot, underscore and dash"
        )
    path = Path(raw_path)
    if not path.is_absolute():
        raise ValueError("repo source path must be absolute")
    return label, path


def verify_production_source_snapshot(
    *,
    snapshot_directory: str | Path,
    repo_source_specs: Iterable[str] = (),
) -> ProductionSourceVerification:
    root = Path(snapshot_directory).resolve()
    manifest_path = root / "manifest.json"
    if not root.is_dir():
        raise ValueError(f"snapshot directory does not exist: {root}")
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("snapshot manifest.json is missing or is a symlink")

    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("snapshot manifest.json is invalid") from exc

    if not isinstance(manifest, dict):
        raise ValueError("snapshot manifest must be a JSON object")
    if int(manifest.get("format_version", -1)) != 1:
        raise ValueError("unsupported production source snapshot format")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("snapshot manifest contains no sources")

    by_label: dict[str, dict[str, object]] = {}
    captured_names: set[str] = set()
    for row in sources:
        if not isinstance(row, dict):
            raise ValueError("snapshot source entry must be an object")
        label = str(row.get("label", ""))
        if not label or not LABEL_RE.fullmatch(label):
            raise ValueError("snapshot source label is invalid")
        if label in by_label:
            raise ValueError(f"duplicate snapshot source label: {label}")

        captured_name = str(row.get("captured_path", ""))
        captured_rel = Path(captured_name)
        if (
            not captured_name
            or captured_rel.is_absolute()
            or len(captured_rel.parts) != 1
            or captured_name in {".", ".."}
        ):
            raise ValueError(
                f"unsafe captured path for {label}: {captured_name}"
            )
        if captured_name in captured_names:
            raise ValueError(
                f"duplicate captured path in snapshot: {captured_name}"
            )
        captured_names.add(captured_name)

        expected_hash = str(row.get("sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError(
                f"invalid SHA-256 in snapshot manifest for {label}"
            )
        size_raw = row.get("size_bytes")
        if isinstance(size_raw, bool) or not isinstance(size_raw, int):
            raise ValueError(
                f"invalid size_bytes in snapshot manifest for {label}"
            )
        if size_raw < 0:
            raise ValueError(
                f"negative size_bytes in snapshot manifest for {label}"
            )

        captured = root / captured_name
        if captured.is_symlink() or not captured.is_file():
            raise ValueError(
                f"captured source is missing or not a regular file: "
                f"{captured_name}"
            )
        if captured.stat().st_size != size_raw:
            raise ValueError(
                f"captured source size mismatch for {label}"
            )
        if _sha256(captured) != expected_hash:
            raise ValueError(
                f"captured source hash mismatch for {label}"
            )
        by_label[label] = row

    imports: dict[str, Path] = {}
    for spec in repo_source_specs:
        label, path = _parse_import_spec(spec)
        if label in imports:
            raise ValueError(f"duplicate repo source label: {label}")
        if label not in by_label:
            raise ValueError(
                f"repo source label is not present in snapshot: {label}"
            )
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"repo source is missing or not a regular file: {path}"
            )
        imports[label] = path.resolve(strict=True)

    verified_rows: list[VerifiedSource] = []
    for label in sorted(by_label):
        row = by_label[label]
        expected_hash = str(row["sha256"])
        imported = imports.get(label)
        if imported is not None and _sha256(imported) != expected_hash:
            raise ValueError(
                f"repo source hash mismatch for {label}: {imported}"
            )
        verified_rows.append(
            VerifiedSource(
                label=label,
                captured_path=str(row["captured_path"]),
                sha256=expected_hash,
                imported_path=(
                    str(imported) if imported is not None else None
                ),
            )
        )

    return ProductionSourceVerification(
        snapshot_directory=str(root),
        format_version=1,
        sources_verified=len(verified_rows),
        imports_verified=len(imports),
        verified=True,
        sources=tuple(verified_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify an exact Phase-2 production-source snapshot and, "
            "optionally, prove imported repo files still match its hashes. "
            "This command is read-only."
        )
    )
    parser.add_argument(
        "--snapshot",
        required=True,
        help="Snapshot directory containing manifest.json",
    )
    parser.add_argument(
        "--repo-source",
        action="append",
        default=[],
        metavar="LABEL=/absolute/path",
        help=(
            "Optional imported repo file to compare to the captured hash. "
            "Repeat per label."
        ),
    )
    args = parser.parse_args()

    result = verify_production_source_snapshot(
        snapshot_directory=args.snapshot,
        repo_source_specs=args.repo_source,
    )
    print(json.dumps(result.to_record(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
