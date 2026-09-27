#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
from typing import Iterable


LABEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class CapturedSource:
    label: str
    source_path: str
    captured_path: str
    sha256: str
    size_bytes: int
    mode_octal: str
    uid: int
    gid: int
    mtime_ns: int


@dataclass(frozen=True)
class ProductionSourceSnapshot:
    format_version: int
    captured_at: str
    output_directory: str
    sources: tuple[CapturedSource, ...]

    def to_record(self) -> dict[str, object]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_source_spec(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise ValueError(
            "source must use LABEL=/absolute/path form"
        )
    label, raw_path = value.split("=", 1)
    label = label.strip()
    raw_path = raw_path.strip()
    if not label or not LABEL_RE.fullmatch(label):
        raise ValueError(
            "source label may contain only letters, numbers, dot, underscore and dash"
        )
    path = Path(raw_path)
    if not path.is_absolute():
        raise ValueError("source path must be absolute")
    return label, path


def capture_production_sources(
    *,
    source_specs: Iterable[str],
    output_directory: str | Path,
    captured_at: str | None = None,
) -> ProductionSourceSnapshot:
    parsed = [_parse_source_spec(value) for value in source_specs]
    if not parsed:
        raise ValueError("at least one --source is required")

    labels = [label for label, _ in parsed]
    if len(labels) != len(set(labels)):
        raise ValueError("source labels must be unique")

    output = Path(output_directory).resolve()
    if output.exists():
        raise ValueError(
            f"output directory already exists: {output}"
        )

    source_rows: list[tuple[str, Path, object]] = []
    for label, source in parsed:
        if source.is_symlink():
            raise ValueError(
                f"source must be an explicit regular file, not a symlink: {source}"
            )
        if not source.is_file():
            raise ValueError(f"source file does not exist: {source}")
        resolved = source.resolve(strict=True)
        source_rows.append((label, resolved, resolved.stat()))

    timestamp = captured_at or datetime.now(timezone.utc).isoformat()
    created = False
    try:
        output.mkdir(parents=True, exist_ok=False)
        created = True
        captured: list[CapturedSource] = []

        for label, source, source_stat in source_rows:
            destination = output / f"{label}__{source.name}"
            shutil.copyfile(source, destination)

            source_hash = _sha256(source)
            captured_hash = _sha256(destination)
            if source_hash != captured_hash:
                raise RuntimeError(
                    f"captured copy checksum mismatch for {source}"
                )

            captured.append(
                CapturedSource(
                    label=label,
                    source_path=str(source),
                    captured_path=destination.name,
                    sha256=source_hash,
                    size_bytes=int(source_stat.st_size),
                    mode_octal=oct(stat.S_IMODE(source_stat.st_mode)),
                    uid=int(source_stat.st_uid),
                    gid=int(source_stat.st_gid),
                    mtime_ns=int(source_stat.st_mtime_ns),
                )
            )

        snapshot = ProductionSourceSnapshot(
            format_version=1,
            captured_at=timestamp,
            output_directory=str(output),
            sources=tuple(captured),
        )
        manifest = output / "manifest.json"
        manifest.write_text(
            json.dumps(snapshot.to_record(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # Re-read every captured file after the manifest write so a successful
        # return means the staging directory still matches the source hashes.
        for row in captured:
            if _sha256(output / row.captured_path) != row.sha256:
                raise RuntimeError(
                    f"captured file changed during snapshot: {row.captured_path}"
                )
        return snapshot
    except Exception:
        if created:
            shutil.rmtree(output, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Copy exact production source/unit files into a new staging "
            "directory with SHA-256 provenance. This command never edits "
            "the sources, runs git, or controls systemd services."
        )
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="LABEL=/absolute/path",
        help=(
            "Source to capture. Repeat for detector, watcher, unit fragments "
            "and any systemd drop-ins."
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="New staging directory; must not already exist",
    )
    args = parser.parse_args()

    result = capture_production_sources(
        source_specs=args.source,
        output_directory=args.output,
    )
    print(json.dumps(result.to_record(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
