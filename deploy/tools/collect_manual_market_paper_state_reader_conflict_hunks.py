from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_STATE_READER_CONFLICT_HUNKS_V1"

EXPECTED_PRODUCTION_HEAD = "ebc0b3c8405da30d88a5ee156f31bf041ffb1ad8"
EXPECTED_CURRENT_BLOB = "d1267db6708b91bc8cacabffcd397a380866c79a"
EXPECTED_CONFLICT_COUNT = 2

STATE_READER_PATH = Path("rust-executor/src/state_reader.rs")
STATE_READER_TOOL = Path("deploy/tools/apply_phase2_single_slot_stack_patch.py")
REVIEWED_SOURCE_BLOBS = {
    STATE_READER_TOOL: "a2e4e0c0435f15be7126e253d1861955be6c2ecd",
}

MAX_LINES_PER_SIDE = 160
MAX_TOTAL_CONFLICT_LINES = 600

IDENTITY_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "repository",
    "production_head",
    "path",
    "expected_base_blob",
    "current_blob",
    "expected_current_blob",
    "target_blob",
    "merge_file_returncode",
    "conflict_count",
    "conflicts",
    "scoped_to_conflicts_only",
    "production_file_modified",
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


def _git_blob_sha_bytes(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _git_blob_sha(path: Path) -> str:
    return _git_blob_sha_bytes(path.read_bytes())


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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
            raise ValueError(f"reviewed conflict-hunk artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed conflict-hunk artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed conflict-hunk artifact mismatch: {relative}")


def _run_git_read(
    repository: Path,
    args: list[str],
    *,
    text: bool,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> subprocess.CompletedProcess[Any]:
    return runner(
        ["git", "-c", f"safe.directory={repository}", *args],
        cwd=str(repository),
        text=text,
        capture_output=True,
        check=False,
    )


def _production_head(
    repository: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> str:
    proc = _run_git_read(
        repository,
        ["rev-parse", "HEAD"],
        text=True,
        runner=runner,
    )
    if proc.returncode != 0:
        raise ValueError("cannot read production HEAD")
    value = str(proc.stdout).strip()
    if not value:
        raise ValueError("production HEAD is empty")
    return value


def _git_show_bytes(
    repository: Path,
    revision: str,
    path: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> bytes:
    proc = _run_git_read(
        repository,
        ["show", f"{revision}:{path}"],
        text=False,
        runner=runner,
    )
    if proc.returncode != 0:
        raise ValueError(f"cannot read reviewed production-base bytes: {path}")
    stdout = proc.stdout
    return stdout if isinstance(stdout, bytes) else str(stdout).encode("utf-8")


def _merge_file_diff3(
    *,
    current: bytes,
    base: bytes,
    target: bytes,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> tuple[int, bytes]:
    with tempfile.TemporaryDirectory(prefix="pio-conflict-hunks-") as tmp:
        root = Path(tmp)
        current_path = root / "production-local"
        base_path = root / "reviewed-base"
        target_path = root / "reviewed-target"
        current_path.write_bytes(current)
        base_path.write_bytes(base)
        target_path.write_bytes(target)

        proc = runner(
            [
                "git",
                "merge-file",
                "-p",
                "--diff3",
                "-L",
                "production-local",
                "-L",
                "reviewed-base",
                "-L",
                "reviewed-target",
                str(current_path),
                str(base_path),
                str(target_path),
            ],
            capture_output=True,
            check=False,
        )

    if proc.returncode == 0:
        raise ValueError("state-reader merge unexpectedly became clean")
    if not 1 <= proc.returncode <= 127:
        raise ValueError(
            "state-reader merge-file analysis failed "
            f"(return code {proc.returncode})"
        )
    stdout = proc.stdout
    merged = stdout if isinstance(stdout, bytes) else str(stdout).encode("utf-8")
    return int(proc.returncode), merged


def _parse_diff3_conflicts(merged: bytes) -> list[dict[str, list[str]]]:
    text = merged.decode("utf-8")
    lines = text.splitlines()
    conflicts: list[dict[str, list[str]]] = []
    i = 0

    while i < len(lines):
        if lines[i] != "<<<<<<< production-local":
            i += 1
            continue

        i += 1
        current: list[str] = []
        while i < len(lines) and lines[i] != "||||||| reviewed-base":
            current.append(lines[i])
            i += 1
        if i >= len(lines):
            raise ValueError("unterminated production-local conflict side")

        i += 1
        base: list[str] = []
        while i < len(lines) and lines[i] != "=======":
            base.append(lines[i])
            i += 1
        if i >= len(lines):
            raise ValueError("unterminated reviewed-base conflict side")

        i += 1
        target: list[str] = []
        while i < len(lines) and lines[i] != ">>>>>>> reviewed-target":
            target.append(lines[i])
            i += 1
        if i >= len(lines):
            raise ValueError("unterminated reviewed-target conflict side")

        conflicts.append(
            {
                "production_local": current,
                "reviewed_base": base,
                "reviewed_target": target,
            }
        )
        i += 1

    if not conflicts:
        raise ValueError("merge-file reported conflicts but no diff3 blocks were parsed")
    return conflicts


def _line_range(
    all_lines: list[str],
    block_lines: list[str],
    *,
    start_at: int,
) -> tuple[dict[str, int | None], int]:
    if not block_lines:
        return {"start": None, "end": None}, start_at

    width = len(block_lines)
    for index in range(start_at, len(all_lines) - width + 1):
        if all_lines[index:index + width] == block_lines:
            return {
                "start": index + 1,
                "end": index + width,
            }, index + width

    raise ValueError("cannot map conflict block back to source line range")


def _section_record(
    *,
    lines: list[str],
    line_range: dict[str, int | None],
) -> dict[str, Any]:
    payload = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    return {
        "line_range": line_range,
        "line_count": len(lines),
        "sha256": _sha256_bytes(payload),
        "lines": lines,
    }


def _conflict_records(
    *,
    current: bytes,
    base: bytes,
    target: bytes,
    parsed: list[dict[str, list[str]]],
) -> list[dict[str, Any]]:
    current_lines = current.decode("utf-8").splitlines()
    base_lines = base.decode("utf-8").splitlines()
    target_lines = target.decode("utf-8").splitlines()

    cursors = {
        "production_local": 0,
        "reviewed_base": 0,
        "reviewed_target": 0,
    }
    source_lines = {
        "production_local": current_lines,
        "reviewed_base": base_lines,
        "reviewed_target": target_lines,
    }

    records: list[dict[str, Any]] = []
    total_lines = 0
    for index, conflict in enumerate(parsed, start=1):
        sections: dict[str, Any] = {}
        for side in ("production_local", "reviewed_base", "reviewed_target"):
            block_lines = conflict[side]
            if len(block_lines) > MAX_LINES_PER_SIDE:
                raise ValueError(
                    f"conflict {index} {side} exceeds bounded line limit"
                )
            total_lines += len(block_lines)
            line_range, next_cursor = _line_range(
                source_lines[side],
                block_lines,
                start_at=cursors[side],
            )
            cursors[side] = next_cursor
            sections[side] = _section_record(
                lines=block_lines,
                line_range=line_range,
            )

        conflict_identity = {
            "index": index,
            **sections,
        }
        records.append(
            {
                **conflict_identity,
                "conflict_sha256": hashlib.sha256(
                    _canonical_bytes(conflict_identity)
                ).hexdigest(),
            }
        )

    if total_lines > MAX_TOTAL_CONFLICT_LINES:
        raise ValueError("combined conflict content exceeds bounded line limit")
    return records


def validate_conflict_hunk_evidence(evidence: dict[str, Any]) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("conflict-hunk evidence must be a JSON object")

    expected_keys = set(IDENTITY_FIELDS) | {"evidence_sha256"}
    if set(evidence) != expected_keys:
        raise ValueError("conflict-hunk evidence fields do not match reviewed schema")
    if evidence.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported conflict-hunk evidence format")
    if evidence.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected conflict-hunk evidence artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if evidence.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("conflict-hunk evidence source lineage mismatch")
    if evidence.get("production_head") != EXPECTED_PRODUCTION_HEAD:
        raise ValueError("conflict-hunk production HEAD mismatch")
    if evidence.get("path") != str(STATE_READER_PATH):
        raise ValueError("conflict-hunk path scope mismatch")
    if evidence.get("current_blob") != EXPECTED_CURRENT_BLOB:
        raise ValueError("conflict-hunk current blob mismatch")
    if evidence.get("expected_current_blob") != EXPECTED_CURRENT_BLOB:
        raise ValueError("conflict-hunk expected-current binding mismatch")

    for field in ("expected_base_blob", "current_blob", "expected_current_blob", "target_blob"):
        if not _is_hex_digest(evidence.get(field), 40):
            raise ValueError(f"conflict-hunk {field} is invalid")

    returncode = evidence.get("merge_file_returncode")
    if (
        not isinstance(returncode, int)
        or isinstance(returncode, bool)
        or not 1 <= returncode <= 127
    ):
        raise ValueError("conflict-hunk merge-file return code is invalid")

    conflicts = evidence.get("conflicts")
    if not isinstance(conflicts, list) or len(conflicts) != EXPECTED_CONFLICT_COUNT:
        raise ValueError("conflict-hunk evidence conflict count is unexpected")
    if evidence.get("conflict_count") != len(conflicts):
        raise ValueError("conflict-hunk evidence count mismatch")

    total_lines = 0
    for expected_index, item in enumerate(conflicts, start=1):
        if not isinstance(item, dict):
            raise ValueError("conflict-hunk entry must be an object")
        expected_fields = {
            "index",
            "production_local",
            "reviewed_base",
            "reviewed_target",
            "conflict_sha256",
        }
        if set(item) != expected_fields:
            raise ValueError("conflict-hunk entry schema mismatch")
        if item["index"] != expected_index:
            raise ValueError("conflict-hunk indexes are not contiguous")

        for side in ("production_local", "reviewed_base", "reviewed_target"):
            section = item[side]
            if not isinstance(section, dict):
                raise ValueError("conflict-hunk section must be an object")
            if set(section) != {"line_range", "line_count", "sha256", "lines"}:
                raise ValueError("conflict-hunk section schema mismatch")
            lines = section["lines"]
            if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
                raise ValueError("conflict-hunk lines must be strings")
            if section["line_count"] != len(lines):
                raise ValueError("conflict-hunk line count mismatch")
            if len(lines) > MAX_LINES_PER_SIDE:
                raise ValueError("conflict-hunk side exceeds bounded line limit")
            total_lines += len(lines)

            line_range = section["line_range"]
            if (
                not isinstance(line_range, dict)
                or set(line_range) != {"start", "end"}
            ):
                raise ValueError("conflict-hunk line range is invalid")
            if lines:
                if (
                    not isinstance(line_range["start"], int)
                    or not isinstance(line_range["end"], int)
                    or line_range["start"] < 1
                    or line_range["end"] < line_range["start"]
                    or line_range["end"] - line_range["start"] + 1 != len(lines)
                ):
                    raise ValueError("conflict-hunk populated line range is invalid")
            else:
                if line_range != {"start": None, "end": None}:
                    raise ValueError("conflict-hunk empty line range must be null")

            payload = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
            if section["sha256"] != _sha256_bytes(payload):
                raise ValueError("conflict-hunk section digest mismatch")

        conflict_identity = {
            "index": item["index"],
            "production_local": item["production_local"],
            "reviewed_base": item["reviewed_base"],
            "reviewed_target": item["reviewed_target"],
        }
        expected_conflict_sha = hashlib.sha256(
            _canonical_bytes(conflict_identity)
        ).hexdigest()
        if item["conflict_sha256"] != expected_conflict_sha:
            raise ValueError("conflict-hunk conflict digest mismatch")

    if total_lines > MAX_TOTAL_CONFLICT_LINES:
        raise ValueError("conflict-hunk total content exceeds bounded line limit")
    if evidence.get("scoped_to_conflicts_only") is not True:
        raise ValueError("conflict-hunk evidence must be conflict-scoped")
    if evidence.get("production_file_modified") is not False:
        raise ValueError("conflict-hunk evidence must not modify production")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if evidence.get(field) is not False:
            raise ValueError(f"conflict-hunk evidence requires {field}=false")

    if not _is_hex_digest(evidence.get("evidence_sha256"), 64):
        raise ValueError("conflict-hunk evidence digest is invalid")
    identity = {field: evidence[field] for field in IDENTITY_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if evidence["evidence_sha256"] != expected_digest:
        raise ValueError("conflict-hunk evidence digest mismatch")


def build_conflict_hunk_evidence(
    *,
    repository: str | Path,
    source_tree: str | Path,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> dict[str, Any]:
    repo = Path(repository).resolve()
    source = Path(source_tree).resolve()

    if not (repo / ".git").exists():
        raise ValueError(f"production repository is not a git tree: {repo}")
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")

    _verify_reviewed_source(source)
    head = _production_head(repo, runner=runner)
    if head != EXPECTED_PRODUCTION_HEAD:
        raise ValueError(f"production HEAD changed from reviewed baseline: {head}")

    state_module = _load_module(
        source / STATE_READER_TOOL,
        "manual_market_paper_state_reader_conflict_hunks_guard",
    )
    expected_base_blob = str(state_module.STACK_BASE_BLOB_SHA)
    target_blob = str(state_module.STACK_TARGET_BLOB_SHA)

    production_path = repo / STATE_READER_PATH
    target_path = source / STATE_READER_PATH
    if production_path.is_symlink() or not production_path.is_file():
        raise ValueError("production state reader is not a regular file")
    if target_path.is_symlink() or not target_path.is_file():
        raise ValueError("reviewed state-reader target is not a regular file")

    current = production_path.read_bytes()
    base = _git_show_bytes(
        repo,
        head,
        str(STATE_READER_PATH),
        runner=runner,
    )
    target = target_path.read_bytes()

    base_blob = _git_blob_sha_bytes(base)
    current_blob = _git_blob_sha_bytes(current)
    actual_target_blob = _git_blob_sha_bytes(target)
    if base_blob != expected_base_blob:
        raise ValueError("production state-reader base blob mismatch")
    if current_blob != EXPECTED_CURRENT_BLOB:
        raise ValueError(
            "production state-reader bytes changed since sealed conflict evidence"
        )
    if actual_target_blob != target_blob:
        raise ValueError("reviewed state-reader target blob mismatch")

    returncode, merged = _merge_file_diff3(
        current=current,
        base=base,
        target=target,
        runner=runner,
    )
    parsed = _parse_diff3_conflicts(merged)
    if len(parsed) != EXPECTED_CONFLICT_COUNT:
        raise ValueError(
            "state-reader conflict count changed from reviewed evidence: "
            f"{len(parsed)}"
        )
    conflicts = _conflict_records(
        current=current,
        base=base,
        target=target,
        parsed=parsed,
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
        "repository": str(repo),
        "production_head": head,
        "path": str(STATE_READER_PATH),
        "expected_base_blob": expected_base_blob,
        "current_blob": current_blob,
        "expected_current_blob": EXPECTED_CURRENT_BLOB,
        "target_blob": target_blob,
        "merge_file_returncode": returncode,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "scoped_to_conflicts_only": True,
        "production_file_modified": False,
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
    validate_conflict_hunk_evidence(evidence)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Emit only the two exact diff3 conflict blocks between the reviewed "
            "state-reader baseline, current production-local bytes, and reviewed "
            "target. The tool is read-only with respect to /opt/pio and fails "
            "closed if HEAD/current bytes/conflict count drift from the sealed "
            "production evidence."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    args = parser.parse_args()

    evidence = build_conflict_hunk_evidence(
        repository=args.repo,
        source_tree=args.source_tree,
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
