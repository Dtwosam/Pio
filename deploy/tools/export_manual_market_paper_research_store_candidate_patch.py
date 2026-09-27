from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_RESEARCH_STORE_PORTABLE_PATCH_V1"

BUILDER_TOOL = Path("deploy/tools/build_manual_market_paper_research_store_candidate.py")
RESEARCH_STORE_PATH = Path(
    "python-learner/src/meteora_learner/research_store.py"
)

REVIEWED_SOURCE_BLOBS = {
    BUILDER_TOOL: "f874b5d0aca746d0d0e66622b6aef7c197e7c3b1",
}

EXPECTED_TARGET_BLOB = "c9b9de5838d95a86bddffa6166b4d7a62e91cf16"
EXPECTED_CANDIDATE_BLOB = "31bd88e3d74490f5d0b617ff4b36383e7e12e18f"

MAX_PATCH_BYTES = 16384
MAX_PATCH_HUNKS = 20
MAX_PATCH_CHANGED_LINES = 256

IDENTITY_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "reviewed_target_path",
    "reviewed_target_blob",
    "candidate_report_sha256",
    "candidate_git_blob",
    "candidate_sha256",
    "candidate_size",
    "patch_sha256",
    "patch_size",
    "patch_hunks",
    "patch_added_lines",
    "patch_removed_lines",
    "patch_output_path",
    "patch_output_under_var_tmp",
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
            raise ValueError(f"reviewed research-store portable artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed research-store portable artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed research-store portable artifact mismatch: {relative}")


def _safe_output_path(raw: str) -> Path:
    output = Path(raw)
    if not output.is_absolute():
        raise ValueError("patch output path must be absolute")
    resolved = output.resolve(strict=False)
    var_tmp = Path("/var/tmp").resolve()
    if resolved == var_tmp or var_tmp not in resolved.parents:
        raise ValueError("patch output path must be under /var/tmp")
    if output.exists():
        raise ValueError("patch output path already exists")
    if output.is_symlink():
        raise ValueError("patch output path must not be a symlink")
    if not output.parent.exists() or not output.parent.is_dir():
        raise ValueError("patch output parent must already exist")
    if output.parent.is_symlink():
        raise ValueError("patch output parent must not be a symlink")
    return resolved


def _build_patch(target: bytes, candidate: bytes) -> bytes:
    patch = "".join(
        difflib.unified_diff(
            target.decode("utf-8").splitlines(keepends=True),
            candidate.decode("utf-8").splitlines(keepends=True),
            fromfile=f"a/{RESEARCH_STORE_PATH}",
            tofile=f"b/{RESEARCH_STORE_PATH}",
            n=3,
        )
    ).encode("utf-8")
    if not patch:
        raise ValueError("research-store candidate patch is unexpectedly empty")
    return patch


def _patch_stats(patch: bytes) -> dict[str, int | str]:
    hunks = 0
    added = 0
    removed = 0
    for line in patch.decode("utf-8").splitlines():
        if line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {
        "sha256": _sha256_bytes(patch),
        "size": len(patch),
        "hunks": hunks,
        "added_lines": added,
        "removed_lines": removed,
    }


def validate_portable_patch_report(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("research-store portable-patch report must be a JSON object")

    expected_keys = set(IDENTITY_FIELDS) | {"report_sha256"}
    if set(report) != expected_keys:
        raise ValueError("research-store portable-patch fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported research-store portable-patch format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected research-store portable-patch artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("research-store portable-patch source lineage mismatch")
    if report.get("reviewed_target_path") != str(RESEARCH_STORE_PATH):
        raise ValueError("research-store portable-patch target path mismatch")
    if report.get("reviewed_target_blob") != EXPECTED_TARGET_BLOB:
        raise ValueError("research-store portable-patch target blob mismatch")
    if report.get("candidate_git_blob") != EXPECTED_CANDIDATE_BLOB:
        raise ValueError("research-store portable-patch candidate blob mismatch")

    for field, length in (
        ("candidate_report_sha256", 64),
        ("candidate_sha256", 64),
        ("patch_sha256", 64),
        ("report_sha256", 64),
    ):
        if not _is_hex_digest(report.get(field), length):
            raise ValueError(f"research-store portable-patch {field} is invalid")

    for field in (
        "candidate_size",
        "patch_size",
        "patch_hunks",
        "patch_added_lines",
        "patch_removed_lines",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"research-store portable-patch {field} is invalid")

    if report["candidate_size"] <= 0:
        raise ValueError("research-store portable-patch candidate size must be positive")
    if report["patch_size"] > MAX_PATCH_BYTES:
        raise ValueError("research-store portable-patch exceeds byte bound")
    if report["patch_hunks"] > MAX_PATCH_HUNKS:
        raise ValueError("research-store portable-patch exceeds hunk bound")
    if (
        report["patch_added_lines"] + report["patch_removed_lines"]
        > MAX_PATCH_CHANGED_LINES
    ):
        raise ValueError("research-store portable-patch exceeds changed-line bound")

    output_path = report.get("patch_output_path")
    if not isinstance(output_path, str) or not output_path.startswith("/var/tmp/"):
        raise ValueError("research-store portable-patch output path is invalid")
    if report.get("patch_output_under_var_tmp") is not True:
        raise ValueError("research-store portable-patch output scope is invalid")
    if report.get("production_file_modified") is not False:
        raise ValueError("research-store portable-patch must not modify production")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if report.get(field) is not False:
            raise ValueError(f"research-store portable-patch requires {field}=false")

    identity = {field: report[field] for field in IDENTITY_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["report_sha256"] != expected_digest:
        raise ValueError("research-store portable-patch report digest mismatch")


def build_portable_patch(
    *,
    source_tree: str | Path,
    candidate_report_path: str | Path,
    patch_output: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")
    _verify_reviewed_source(source)

    builder = _load_module(
        source / BUILDER_TOOL,
        "manual_market_paper_research_store_portable_builder",
    )

    candidate_report = json.loads(
        Path(candidate_report_path).read_text(encoding="utf-8")
    )
    builder.validate_candidate_report(candidate_report)
    if candidate_report.get("candidate_git_blob") != EXPECTED_CANDIDATE_BLOB:
        raise ValueError("research-store candidate report is not the sealed candidate")

    candidate_path = Path(str(candidate_report["output_path"]))
    candidate_resolved = candidate_path.resolve(strict=True)
    var_tmp = Path("/var/tmp").resolve()
    if var_tmp not in candidate_resolved.parents:
        raise ValueError("research-store candidate file escaped /var/tmp")
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise ValueError("research-store candidate file is not a regular file")

    candidate = candidate_path.read_bytes()
    if _git_blob_sha_bytes(candidate) != EXPECTED_CANDIDATE_BLOB:
        raise ValueError("research-store candidate Git blob no longer matches sealed identity")
    if _sha256_bytes(candidate) != candidate_report["candidate_sha256"]:
        raise ValueError("research-store candidate SHA-256 no longer matches sealed identity")
    if len(candidate) != candidate_report["candidate_size"]:
        raise ValueError("research-store candidate size no longer matches sealed identity")

    target_path = source / RESEARCH_STORE_PATH
    if target_path.is_symlink() or not target_path.is_file():
        raise ValueError("reviewed research-store target is not a regular file")
    target = target_path.read_bytes()
    if _git_blob_sha_bytes(target) != EXPECTED_TARGET_BLOB:
        raise ValueError("reviewed research-store target blob changed")

    patch = _build_patch(target, candidate)
    stats = _patch_stats(patch)
    if stats["size"] > MAX_PATCH_BYTES:
        raise ValueError("research-store candidate patch exceeds byte bound")
    if stats["hunks"] > MAX_PATCH_HUNKS:
        raise ValueError("research-store candidate patch exceeds hunk bound")
    if stats["added_lines"] + stats["removed_lines"] > MAX_PATCH_CHANGED_LINES:
        raise ValueError("research-store candidate patch exceeds changed-line bound")

    output = _safe_output_path(patch_output)
    output.write_bytes(patch)
    if _sha256_bytes(output.read_bytes()) != stats["sha256"]:
        raise ValueError("research-store portable patch output verification failed")

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
        "reviewed_target_path": str(RESEARCH_STORE_PATH),
        "reviewed_target_blob": EXPECTED_TARGET_BLOB,
        "candidate_report_sha256": candidate_report["report_sha256"],
        "candidate_git_blob": EXPECTED_CANDIDATE_BLOB,
        "candidate_sha256": candidate_report["candidate_sha256"],
        "candidate_size": candidate_report["candidate_size"],
        "patch_sha256": stats["sha256"],
        "patch_size": stats["size"],
        "patch_hunks": stats["hunks"],
        "patch_added_lines": stats["added_lines"],
        "patch_removed_lines": stats["removed_lines"],
        "patch_output_path": str(output),
        "patch_output_under_var_tmp": True,
        "production_file_modified": False,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    report = {
        **identity,
        "report_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }
    validate_portable_patch_report(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Export the exact sealed research_store.py candidate as a bounded "
            "unified patch against the reviewed target. The tool has no "
            "production repository input and writes only one patch under /var/tmp."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--candidate-report", required=True)
    parser.add_argument("--patch-output", required=True)
    args = parser.parse_args()

    report = build_portable_patch(
        source_tree=args.source_tree,
        candidate_report_path=args.candidate_report,
        patch_output=args.patch_output,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
