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
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_STATE_READER_CANDIDATE_V1"

HUNK_TOOL = Path("deploy/tools/collect_manual_market_paper_state_reader_conflict_hunks.py")
REVIEWED_SOURCE_BLOBS = {
    HUNK_TOOL: "436c9726d271fcaf5b0c2a409967c758c6cc6e5a",
}

EXPECTED_HUNK_EVIDENCE_SHA256 = (
    "805d5e89ecc9d14157a58e5fc40e1cf3b9632b6636fc61580f157890eb4736a7"
)
EXPECTED_PRODUCTION_HEAD = "ebc0b3c8405da30d88a5ee156f31bf041ffb1ad8"
EXPECTED_CURRENT_BLOB = "d1267db6708b91bc8cacabffcd397a380866c79a"
EXPECTED_TARGET_BLOB = "30d1435af1329bca07f73d6639539b43503e84e9"
EXPECTED_PATH = "rust-executor/src/state_reader.rs"

EXPECTED_CONFLICTS = (
    {
        "index": 1,
        "conflict_sha256": "69e9c0fe8a2f2658361919a3e7474a101a00c6a23d60d77cf40218e57d2d98d2",
        "production_local_sha256": "9c7192be386966f3e08082a53b32fc4ce7685649108b5c69603668ee22714d11",
        "reviewed_base_sha256": "2890e83b9f94d082b7775c381bf102bb1c9fa7544e60271ea681ef68c1f73b15",
        "reviewed_target_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resolution": "REVIEWED_TARGET",
    },
    {
        "index": 2,
        "conflict_sha256": "84fd20c6077a53a98159666b221d9162b69076964fec93f7955728208fe74693",
        "production_local_sha256": "f881dc06f361374aa231646d211fcb7f693a07f0bee2a897e44898626de50124",
        "reviewed_base_sha256": "6570e6d95d7200354e4c3421eb068b9cd938bd2f83e6bf49404d7ac11a9b67c4",
        "reviewed_target_sha256": "b2e90aa4ae2bbc738867bc9fea6177691bc2ac470f7eab50cfd305525d003b05",
        "resolution": "REVIEWED_TARGET",
    },
)

IDENTITY_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "repository",
    "production_head",
    "source_hunk_evidence_sha256",
    "path",
    "current_blob",
    "target_blob",
    "resolutions",
    "candidate_git_blob",
    "candidate_sha256",
    "candidate_size",
    "candidate_diff_from_current",
    "candidate_diff_from_target",
    "output_path",
    "output_under_var_tmp",
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
            raise ValueError(f"reviewed candidate artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed candidate artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed candidate artifact mismatch: {relative}")


def _section_hash(section: dict[str, Any]) -> str:
    value = section.get("sha256")
    if not isinstance(value, str):
        raise ValueError("conflict section digest is missing")
    return value


def _validate_live_conflict_binding(evidence: dict[str, Any]) -> None:
    if evidence.get("evidence_sha256") != EXPECTED_HUNK_EVIDENCE_SHA256:
        raise ValueError("state-reader conflict-hunk evidence changed")
    if evidence.get("production_head") != EXPECTED_PRODUCTION_HEAD:
        raise ValueError("production HEAD changed")
    if evidence.get("current_blob") != EXPECTED_CURRENT_BLOB:
        raise ValueError("production state-reader blob changed")
    if evidence.get("target_blob") != EXPECTED_TARGET_BLOB:
        raise ValueError("reviewed state-reader target blob changed")

    conflicts = evidence.get("conflicts")
    if not isinstance(conflicts, list) or len(conflicts) != len(EXPECTED_CONFLICTS):
        raise ValueError("state-reader conflict count changed")

    for expected, actual in zip(EXPECTED_CONFLICTS, conflicts, strict=True):
        if actual.get("index") != expected["index"]:
            raise ValueError("state-reader conflict ordering changed")
        if actual.get("conflict_sha256") != expected["conflict_sha256"]:
            raise ValueError(f"state-reader conflict {expected['index']} changed")
        if _section_hash(actual["production_local"]) != expected["production_local_sha256"]:
            raise ValueError(
                f"state-reader conflict {expected['index']} production-local side changed"
            )
        if _section_hash(actual["reviewed_base"]) != expected["reviewed_base_sha256"]:
            raise ValueError(
                f"state-reader conflict {expected['index']} reviewed-base side changed"
            )
        if _section_hash(actual["reviewed_target"]) != expected["reviewed_target_sha256"]:
            raise ValueError(
                f"state-reader conflict {expected['index']} reviewed-target side changed"
            )


def _resolve_diff3_to_reviewed_target(
    merged: bytes,
    conflicts: list[dict[str, Any]],
) -> bytes:
    lines = merged.decode("utf-8").splitlines(keepends=True)
    output: list[str] = []
    i = 0
    conflict_index = 0

    while i < len(lines):
        if lines[i].rstrip("\r\n") != "<<<<<<< production-local":
            output.append(lines[i])
            i += 1
            continue

        if conflict_index >= len(conflicts):
            raise ValueError("merge output contains unexpected extra conflict")
        expected = conflicts[conflict_index]
        conflict_index += 1

        i += 1
        while i < len(lines) and lines[i].rstrip("\r\n") != "||||||| reviewed-base":
            i += 1
        if i >= len(lines):
            raise ValueError("unterminated production-local conflict")

        i += 1
        while i < len(lines) and lines[i].rstrip("\r\n") != "=======":
            i += 1
        if i >= len(lines):
            raise ValueError("unterminated reviewed-base conflict")

        i += 1
        target_lines: list[str] = []
        while i < len(lines) and lines[i].rstrip("\r\n") != ">>>>>>> reviewed-target":
            target_lines.append(lines[i])
            i += 1
        if i >= len(lines):
            raise ValueError("unterminated reviewed-target conflict")

        expected_target = expected["reviewed_target"]["lines"]
        actual_target = [line.rstrip("\r\n") for line in target_lines]
        if actual_target != expected_target:
            raise ValueError(
                f"merge output conflict {expected['index']} target side drifted"
            )
        output.extend(target_lines)
        i += 1

    if conflict_index != len(conflicts):
        raise ValueError("merge output omitted an expected conflict")
    return "".join(output).encode("utf-8")


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

    added = 0
    removed = 0
    hunks = 0
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


def validate_candidate_report(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("candidate report must be a JSON object")

    expected_keys = set(IDENTITY_FIELDS) | {"report_sha256"}
    if set(report) != expected_keys:
        raise ValueError("candidate report fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported candidate report format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected candidate report artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("candidate report source lineage mismatch")
    if report.get("production_head") != EXPECTED_PRODUCTION_HEAD:
        raise ValueError("candidate report production HEAD mismatch")
    if report.get("source_hunk_evidence_sha256") != EXPECTED_HUNK_EVIDENCE_SHA256:
        raise ValueError("candidate report hunk evidence mismatch")
    if report.get("current_blob") != EXPECTED_CURRENT_BLOB:
        raise ValueError("candidate report current blob mismatch")
    if report.get("target_blob") != EXPECTED_TARGET_BLOB:
        raise ValueError("candidate report target blob mismatch")
    if report.get("path") != EXPECTED_PATH:
        raise ValueError("candidate report path scope mismatch")

    repository = report.get("repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("candidate report repository must be absolute")

    resolutions = report.get("resolutions")
    if not isinstance(resolutions, list) or len(resolutions) != 2:
        raise ValueError("candidate report resolutions are invalid")
    for expected, actual in zip(EXPECTED_CONFLICTS, resolutions, strict=True):
        if actual != {
            "index": expected["index"],
            "conflict_sha256": expected["conflict_sha256"],
            "resolution": "REVIEWED_TARGET",
        }:
            raise ValueError("candidate report conflict resolution mismatch")

    for field, length in (
        ("candidate_git_blob", 40),
        ("candidate_sha256", 64),
        ("report_sha256", 64),
    ):
        if not _is_hex_digest(report.get(field), length):
            raise ValueError(f"candidate report {field} is invalid")

    if not isinstance(report.get("candidate_size"), int) or report["candidate_size"] <= 0:
        raise ValueError("candidate report size is invalid")

    for field in ("candidate_diff_from_current", "candidate_diff_from_target"):
        stats = report.get(field)
        if not isinstance(stats, dict):
            raise ValueError(f"candidate report {field} is invalid")
        if set(stats) != {"sha256", "size", "hunks", "added_lines", "removed_lines"}:
            raise ValueError(f"candidate report {field} schema mismatch")
        if not _is_hex_digest(stats.get("sha256"), 64):
            raise ValueError(f"candidate report {field} digest is invalid")
        for key in ("size", "hunks", "added_lines", "removed_lines"):
            if (
                not isinstance(stats.get(key), int)
                or isinstance(stats.get(key), bool)
                or stats[key] < 0
            ):
                raise ValueError(f"candidate report {field} {key} is invalid")

    output_path = report.get("output_path")
    if not isinstance(output_path, str) or not output_path.startswith("/var/tmp/"):
        raise ValueError("candidate report output path is invalid")
    if report.get("output_under_var_tmp") is not True:
        raise ValueError("candidate report output scope is invalid")
    if report.get("production_file_modified") is not False:
        raise ValueError("candidate report must not claim production modification")
    if report.get("candidate_requires_isolated_validation") is not True:
        raise ValueError("candidate report must require isolated validation")
    if report.get("requires_separate_mutation_authorization") is not True:
        raise ValueError("candidate report must require separate mutation authorization")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if report.get(field) is not False:
            raise ValueError(f"candidate report requires {field}=false")

    identity = {field: report[field] for field in IDENTITY_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["report_sha256"] != expected_digest:
        raise ValueError("candidate report digest mismatch")


def build_candidate(
    *,
    repository: str | Path,
    source_tree: str | Path,
    output_path: str,
) -> dict[str, Any]:
    repo = Path(repository).resolve()
    source = Path(source_tree).resolve()
    if not (repo / ".git").exists():
        raise ValueError(f"production repository is not a git tree: {repo}")
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")

    _verify_reviewed_source(source)
    hunk_module = _load_module(
        source / HUNK_TOOL,
        "manual_market_paper_state_reader_candidate_hunks",
    )
    evidence = hunk_module.build_conflict_hunk_evidence(
        repository=repo,
        source_tree=source,
    )
    hunk_module.validate_conflict_hunk_evidence(evidence)
    _validate_live_conflict_binding(evidence)

    state_path = Path(evidence["path"])
    current_path = repo / state_path
    target_path = source / state_path
    current = current_path.read_bytes()
    base = hunk_module._git_show_bytes(
        repo,
        EXPECTED_PRODUCTION_HEAD,
        str(state_path),
    )
    target = target_path.read_bytes()

    returncode, merged = hunk_module._merge_file_diff3(
        current=current,
        base=base,
        target=target,
    )
    if returncode != evidence["merge_file_returncode"]:
        raise ValueError("state-reader merge conflict count drifted")

    candidate = _resolve_diff3_to_reviewed_target(
        merged,
        evidence["conflicts"],
    )
    candidate_blob = _git_blob_sha_bytes(candidate)
    candidate_sha = _sha256_bytes(candidate)

    if candidate_blob in {EXPECTED_CURRENT_BLOB, EXPECTED_TARGET_BLOB}:
        raise ValueError(
            "resolved candidate unexpectedly collapses to current or reviewed target"
        )

    output = _safe_output_path(output_path)
    output.write_bytes(candidate)

    if _git_blob_sha(output) != candidate_blob:
        raise ValueError("candidate output verification failed")

    resolutions = [
        {
            "index": item["index"],
            "conflict_sha256": item["conflict_sha256"],
            "resolution": "REVIEWED_TARGET",
        }
        for item in evidence["conflicts"]
    ]

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
        "production_head": evidence["production_head"],
        "source_hunk_evidence_sha256": evidence["evidence_sha256"],
        "path": evidence["path"],
        "current_blob": evidence["current_blob"],
        "target_blob": evidence["target_blob"],
        "resolutions": resolutions,
        "candidate_git_blob": candidate_blob,
        "candidate_sha256": candidate_sha,
        "candidate_size": len(candidate),
        "candidate_diff_from_current": _unified_patch_stats(current, candidate),
        "candidate_diff_from_target": _unified_patch_stats(target, candidate),
        "output_path": str(output),
        "output_under_var_tmp": True,
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
            "Construct a state-reader preserved-fix candidate outside /opt/pio "
            "by re-running the exact sealed two-conflict analysis, preserving "
            "all automatic non-overlap merges, and resolving only the two exact "
            "conflict hashes to their reviewed-target sides. The candidate may "
            "only be written under /var/tmp and still requires isolated build/"
            "test validation plus separate mutation authorization."
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
