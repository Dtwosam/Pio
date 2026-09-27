from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_RESEARCH_STORE_CANDIDATE_V1"

EXPECTED_PRODUCTION_HEAD = "ebc0b3c8405da30d88a5ee156f31bf041ffb1ad8"
EXPECTED_BASE_BLOB = "16a86b63272ad48f4c185b39ecc2e0c14276ff9f"
EXPECTED_CURRENT_BLOB = "f9deb47c10c88a4e1e364dd12d6c7569c3826a98"
EXPECTED_TARGET_BLOB = "c9b9de5838d95a86bddffa6166b4d7a62e91cf16"
EXPECTED_CANDIDATE_BLOB = "31bd88e3d74490f5d0b617ff4b36383e7e12e18f"

RESEARCH_STORE_PATH = Path(
    "python-learner/src/meteora_learner/research_store.py"
)
ANALYZER_TOOL = Path(
    "deploy/tools/analyze_manual_market_paper_conflict_reconciliation.py"
)
PHASE2_MANIFEST = Path(
    "deploy/manifests/market-paper-phase2-prerequisites.json"
)

REVIEWED_SOURCE_BLOBS = {
    ANALYZER_TOOL: "5c797922480ceb76710995e5913c69af5b03947b",
    PHASE2_MANIFEST: "dfb1eb62241f6fc3b6351e3b40b524fb68f4a61e",
}

IDENTITY_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "repository",
    "production_head",
    "path",
    "base_blob",
    "current_blob",
    "target_blob",
    "candidate_git_blob",
    "candidate_sha256",
    "candidate_size",
    "candidate_diff_from_current",
    "candidate_diff_from_target",
    "output_path",
    "output_under_var_tmp",
    "merge_status",
    "production_file_modified",
    "candidate_requires_isolated_validation",
    "requires_separate_mutation_authorization",
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
            raise ValueError(f"reviewed research-store artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed research-store artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed research-store artifact mismatch: {relative}")


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
    head = str(proc.stdout).strip()
    if head != EXPECTED_PRODUCTION_HEAD:
        raise ValueError(f"production HEAD changed from reviewed baseline: {head}")
    return head


def _safe_output_path(raw: str) -> Path:
    output = Path(raw)
    if not output.is_absolute():
        raise ValueError("candidate output path must be absolute")
    resolved = output.resolve(strict=False)
    var_tmp = Path("/var/tmp").resolve()
    if resolved == var_tmp or var_tmp not in resolved.parents:
        raise ValueError("candidate output path must be under /var/tmp")
    if output.exists():
        raise ValueError("candidate output path already exists")
    if output.is_symlink():
        raise ValueError("candidate output path must not be a symlink")
    if not output.parent.exists() or not output.parent.is_dir():
        raise ValueError("candidate output parent must already exist")
    if output.parent.is_symlink():
        raise ValueError("candidate output parent must not be a symlink")
    return resolved


def _unified_patch_stats(before: bytes, after: bytes) -> dict[str, Any]:
    patch = "".join(
        difflib.unified_diff(
            before.decode("utf-8").splitlines(keepends=True),
            after.decode("utf-8").splitlines(keepends=True),
            fromfile="before",
            tofile="after",
            n=3,
        )
    ).encode("utf-8")
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


def validate_candidate_report(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("research-store candidate report must be a JSON object")

    expected_keys = set(IDENTITY_FIELDS) | {"report_sha256"}
    if set(report) != expected_keys:
        raise ValueError("research-store candidate fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported research-store candidate format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected research-store candidate artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("research-store candidate source lineage mismatch")

    if report.get("production_head") != EXPECTED_PRODUCTION_HEAD:
        raise ValueError("research-store candidate production HEAD mismatch")
    if report.get("path") != str(RESEARCH_STORE_PATH):
        raise ValueError("research-store candidate path scope mismatch")
    if report.get("base_blob") != EXPECTED_BASE_BLOB:
        raise ValueError("research-store candidate base blob mismatch")
    if report.get("current_blob") != EXPECTED_CURRENT_BLOB:
        raise ValueError("research-store candidate current blob mismatch")
    if report.get("target_blob") != EXPECTED_TARGET_BLOB:
        raise ValueError("research-store candidate target blob mismatch")
    if report.get("candidate_git_blob") != EXPECTED_CANDIDATE_BLOB:
        raise ValueError("research-store candidate blob mismatch")

    for field, length in (
        ("candidate_sha256", 64),
        ("report_sha256", 64),
    ):
        if not _is_hex_digest(report.get(field), length):
            raise ValueError(f"research-store candidate {field} is invalid")

    size = report.get("candidate_size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("research-store candidate size is invalid")

    for field in ("candidate_diff_from_current", "candidate_diff_from_target"):
        stats = report.get(field)
        if not isinstance(stats, dict):
            raise ValueError(f"research-store candidate {field} is invalid")
        if set(stats) != {"sha256", "size", "hunks", "added_lines", "removed_lines"}:
            raise ValueError(f"research-store candidate {field} schema mismatch")
        if not _is_hex_digest(stats.get("sha256"), 64):
            raise ValueError(f"research-store candidate {field} digest is invalid")

    output = report.get("output_path")
    if not isinstance(output, str) or not output.startswith("/var/tmp/"):
        raise ValueError("research-store candidate output path is invalid")
    if report.get("output_under_var_tmp") is not True:
        raise ValueError("research-store candidate output scope is invalid")
    if report.get("merge_status") != "CLEAN":
        raise ValueError("research-store candidate merge must be CLEAN")
    if report.get("production_file_modified") is not False:
        raise ValueError("research-store candidate must not modify production")
    if report.get("candidate_requires_isolated_validation") is not True:
        raise ValueError("research-store candidate must require isolated validation")
    if report.get("requires_separate_mutation_authorization") is not True:
        raise ValueError("research-store candidate must require separate mutation authorization")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if report.get(field) is not False:
            raise ValueError(f"research-store candidate requires {field}=false")

    identity = {field: report[field] for field in IDENTITY_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["report_sha256"] != expected_digest:
        raise ValueError("research-store candidate report digest mismatch")


def build_candidate(
    *,
    repository: str | Path,
    source_tree: str | Path,
    output_path: str,
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
    production_path = repo / RESEARCH_STORE_PATH
    target_path = source / RESEARCH_STORE_PATH
    if production_path.is_symlink() or not production_path.is_file():
        raise ValueError("production research store is not a regular file")
    if target_path.is_symlink() or not target_path.is_file():
        raise ValueError("reviewed research-store target is not a regular file")

    current = production_path.read_bytes()
    target = target_path.read_bytes()
    if _git_blob_sha_bytes(current) != EXPECTED_CURRENT_BLOB:
        raise ValueError("production research-store bytes changed")
    if _git_blob_sha_bytes(target) != EXPECTED_TARGET_BLOB:
        raise ValueError("reviewed research-store target changed")

    analyzer = _load_module(
        source / ANALYZER_TOOL,
        "manual_market_paper_research_store_candidate_analyzer",
    )
    base = analyzer._git_show_bytes(
        repo,
        head,
        str(RESEARCH_STORE_PATH),
        runner=runner,
    )
    if _git_blob_sha_bytes(base) != EXPECTED_BASE_BLOB:
        raise ValueError("production research-store base changed")

    merge = analyzer._three_way_merge(
        current=current,
        base=base,
        target=target,
        runner=runner,
    )
    if merge["status"] != "CLEAN":
        raise ValueError("research-store merge is no longer clean")
    candidate = merge["candidate_bytes"]
    if candidate is None:
        raise ValueError("research-store clean merge produced no candidate")
    candidate_blob = _git_blob_sha_bytes(candidate)
    if candidate_blob != EXPECTED_CANDIDATE_BLOB:
        raise ValueError(
            "research-store clean candidate changed from sealed reconciliation"
        )

    output = _safe_output_path(output_path)
    output.write_bytes(candidate)
    if _git_blob_sha(output) != EXPECTED_CANDIDATE_BLOB:
        raise ValueError("research-store candidate output verification failed")

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
        "path": str(RESEARCH_STORE_PATH),
        "base_blob": EXPECTED_BASE_BLOB,
        "current_blob": EXPECTED_CURRENT_BLOB,
        "target_blob": EXPECTED_TARGET_BLOB,
        "candidate_git_blob": candidate_blob,
        "candidate_sha256": _sha256_bytes(candidate),
        "candidate_size": len(candidate),
        "candidate_diff_from_current": _unified_patch_stats(current, candidate),
        "candidate_diff_from_target": _unified_patch_stats(target, candidate),
        "output_path": str(output),
        "output_under_var_tmp": True,
        "merge_status": "CLEAN",
        "production_file_modified": False,
        "candidate_requires_isolated_validation": True,
        "requires_separate_mutation_authorization": True,
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
    validate_candidate_report(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Construct the exact clean three-way research_store.py candidate "
            "outside /opt/pio. The tool is bound to the sealed production HEAD, "
            "base/current/target blobs, and candidate blob from live reconciliation."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report = build_candidate(
        repository=args.repo,
        source_tree=args.source_tree,
        output_path=args.output,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
