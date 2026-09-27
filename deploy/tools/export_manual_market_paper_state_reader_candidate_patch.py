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
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_STATE_READER_PORTABLE_PATCH_V1"

BUILDER_TOOL = Path("deploy/tools/build_manual_market_paper_state_reader_candidate.py")
STATE_READER_PATH = Path("rust-executor/src/state_reader.rs")

REVIEWED_SOURCE_BLOBS = {
    BUILDER_TOOL: "1c97fc8db482de015b2463f6d2a4823e8003628e",
}

EXPECTED_CANDIDATE_REPORT_SHA256 = (
    "de66e27efa3e267f404025fb69e44df620ab7d593eece502f7d65b6f71cc4040"
)
EXPECTED_CANDIDATE_GIT_BLOB = "f54a1021cf8f89d285bde957d1f72d81857ec2fa"
EXPECTED_CANDIDATE_SHA256 = (
    "584aed6e7ec92723e2d73220979e83bbe7fbcf7e19d50b52d05854ce80c33826"
)
EXPECTED_CANDIDATE_SIZE = 31806
EXPECTED_TARGET_BLOB = "30d1435af1329bca07f73d6639539b43503e84e9"

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
            raise ValueError(f"reviewed portable-patch artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed portable-patch artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed portable-patch artifact mismatch: {relative}")


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
            fromfile=f"a/{STATE_READER_PATH}",
            tofile=f"b/{STATE_READER_PATH}",
            n=3,
        )
    ).encode("utf-8")
    if not patch:
        raise ValueError("candidate patch is unexpectedly empty")
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
        raise ValueError("portable-patch report must be a JSON object")

    expected_keys = set(IDENTITY_FIELDS) | {"report_sha256"}
    if set(report) != expected_keys:
        raise ValueError("portable-patch report fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported portable-patch report format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected portable-patch report artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("portable-patch source lineage mismatch")
    if report.get("reviewed_target_path") != str(STATE_READER_PATH):
        raise ValueError("portable-patch target path mismatch")
    if report.get("reviewed_target_blob") != EXPECTED_TARGET_BLOB:
        raise ValueError("portable-patch target blob mismatch")
    if report.get("candidate_report_sha256") != EXPECTED_CANDIDATE_REPORT_SHA256:
        raise ValueError("portable-patch candidate report mismatch")
    if report.get("candidate_git_blob") != EXPECTED_CANDIDATE_GIT_BLOB:
        raise ValueError("portable-patch candidate Git blob mismatch")
    if report.get("candidate_sha256") != EXPECTED_CANDIDATE_SHA256:
        raise ValueError("portable-patch candidate SHA-256 mismatch")
    if report.get("candidate_size") != EXPECTED_CANDIDATE_SIZE:
        raise ValueError("portable-patch candidate size mismatch")

    if not _is_hex_digest(report.get("patch_sha256"), 64):
        raise ValueError("portable-patch digest is invalid")
    for field in (
        "patch_size",
        "patch_hunks",
        "patch_added_lines",
        "patch_removed_lines",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"portable-patch {field} is invalid")

    if report["patch_size"] > MAX_PATCH_BYTES:
        raise ValueError("portable-patch exceeds byte bound")
    if report["patch_hunks"] > MAX_PATCH_HUNKS:
        raise ValueError("portable-patch exceeds hunk bound")
    if (
        report["patch_added_lines"] + report["patch_removed_lines"]
        > MAX_PATCH_CHANGED_LINES
    ):
        raise ValueError("portable-patch exceeds changed-line bound")

    output_path = report.get("patch_output_path")
    if not isinstance(output_path, str) or not output_path.startswith("/var/tmp/"):
        raise ValueError("portable-patch output path is invalid")
    if report.get("patch_output_under_var_tmp") is not True:
        raise ValueError("portable-patch output scope is invalid")
    if report.get("production_file_modified") is not False:
        raise ValueError("portable-patch must not modify production")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if report.get(field) is not False:
            raise ValueError(f"portable-patch requires {field}=false")

    if not _is_hex_digest(report.get("report_sha256"), 64):
        raise ValueError("portable-patch report digest is invalid")
    identity = {field: report[field] for field in IDENTITY_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["report_sha256"] != expected_digest:
        raise ValueError("portable-patch report digest mismatch")


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
        "manual_market_paper_state_reader_portable_patch_builder",
    )

    candidate_report = json.loads(
        Path(candidate_report_path).read_text(encoding="utf-8")
    )
    builder.validate_candidate_report(candidate_report)

    if candidate_report.get("report_sha256") != EXPECTED_CANDIDATE_REPORT_SHA256:
        raise ValueError("candidate report is not the sealed live candidate")
    if candidate_report.get("candidate_git_blob") != EXPECTED_CANDIDATE_GIT_BLOB:
        raise ValueError("candidate report Git blob is not the sealed live candidate")
    if candidate_report.get("candidate_sha256") != EXPECTED_CANDIDATE_SHA256:
        raise ValueError("candidate report SHA-256 is not the sealed live candidate")
    if candidate_report.get("candidate_size") != EXPECTED_CANDIDATE_SIZE:
        raise ValueError("candidate report size is not the sealed live candidate")

    candidate_path = Path(str(candidate_report["output_path"]))
    candidate_resolved = candidate_path.resolve(strict=True)
    var_tmp = Path("/var/tmp").resolve()
    if var_tmp not in candidate_resolved.parents:
        raise ValueError("candidate file escaped /var/tmp")
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise ValueError("candidate file is not a regular file")

    candidate = candidate_path.read_bytes()
    if _git_blob_sha_bytes(candidate) != EXPECTED_CANDIDATE_GIT_BLOB:
        raise ValueError("candidate file Git blob no longer matches sealed identity")
    if _sha256_bytes(candidate) != EXPECTED_CANDIDATE_SHA256:
        raise ValueError("candidate file SHA-256 no longer matches sealed identity")
    if len(candidate) != EXPECTED_CANDIDATE_SIZE:
        raise ValueError("candidate file size no longer matches sealed identity")

    target_path = source / STATE_READER_PATH
    if target_path.is_symlink() or not target_path.is_file():
        raise ValueError("reviewed target is not a regular file")
    target = target_path.read_bytes()
    if _git_blob_sha_bytes(target) != EXPECTED_TARGET_BLOB:
        raise ValueError("reviewed target blob changed")

    patch = _build_patch(target, candidate)
    stats = _patch_stats(patch)
    if stats["size"] > MAX_PATCH_BYTES:
        raise ValueError("candidate patch exceeds byte bound")
    if stats["hunks"] > MAX_PATCH_HUNKS:
        raise ValueError("candidate patch exceeds hunk bound")
    if stats["added_lines"] + stats["removed_lines"] > MAX_PATCH_CHANGED_LINES:
        raise ValueError("candidate patch exceeds changed-line bound")

    output = _safe_output_path(patch_output)
    output.write_bytes(patch)
    if _sha256_bytes(output.read_bytes()) != stats["sha256"]:
        raise ValueError("portable patch output verification failed")

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
        "reviewed_target_path": str(STATE_READER_PATH),
        "reviewed_target_blob": EXPECTED_TARGET_BLOB,
        "candidate_report_sha256": EXPECTED_CANDIDATE_REPORT_SHA256,
        "candidate_git_blob": EXPECTED_CANDIDATE_GIT_BLOB,
        "candidate_sha256": EXPECTED_CANDIDATE_SHA256,
        "candidate_size": EXPECTED_CANDIDATE_SIZE,
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
            "Export the exact sealed state-reader candidate as a small unified "
            "patch against the reviewed target. The tool never reads or writes "
            "/opt/pio; it verifies the existing sealed candidate under /var/tmp "
            "and writes one bounded patch under /var/tmp for external CI review."
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
