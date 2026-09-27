from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_CONFLICT_RECONCILIATION_V1"

EXPECTED_PRODUCTION_HEAD = "ebc0b3c8405da30d88a5ee156f31bf041ffb1ad8"

PHASE2_MANIFEST = Path("deploy/manifests/market-paper-phase2-prerequisites.json")
STATE_READER_TOOL = Path("deploy/tools/apply_phase2_single_slot_stack_patch.py")

RESEARCH_STORE = "python-learner/src/meteora_learner/research_store.py"
STATE_READER = "rust-executor/src/state_reader.rs"

REVIEWED_SOURCE_BLOBS = {
    PHASE2_MANIFEST: "dfb1eb62241f6fc3b6351e3b40b524fb68f4a61e",
    STATE_READER_TOOL: "a2e4e0c0435f15be7126e253d1861955be6c2ecd",
}

IDENTITY_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "repository",
    "production_head",
    "expected_production_head",
    "files",
    "all_merge_clean",
    "candidate_content_emitted",
    "requires_manual_candidate_review",
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
            raise ValueError(f"reviewed reconciliation artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed reconciliation artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed reconciliation artifact mismatch: {relative}")


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
    if isinstance(stdout, bytes):
        return stdout
    return str(stdout).encode("utf-8")


def _unified_patch(
    *,
    from_name: str,
    to_name: str,
    before: bytes,
    after: bytes,
) -> bytes:
    before_text = before.decode("utf-8")
    after_text = after.decode("utf-8")
    lines = difflib.unified_diff(
        before_text.splitlines(keepends=True),
        after_text.splitlines(keepends=True),
        fromfile=from_name,
        tofile=to_name,
        n=3,
    )
    return "".join(lines).encode("utf-8")


def _patch_stats(patch: bytes) -> dict[str, Any]:
    text = patch.decode("utf-8")
    added = 0
    removed = 0
    hunks = 0
    for line in text.splitlines():
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


def _three_way_merge(
    *,
    current: bytes,
    base: bytes,
    target: bytes,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pio-reconcile-") as tmp:
        root = Path(tmp)
        current_path = root / "current"
        base_path = root / "base"
        target_path = root / "target"
        current_path.write_bytes(current)
        base_path.write_bytes(base)
        target_path.write_bytes(target)

        proc = runner(
            [
                "git",
                "merge-file",
                "-p",
                "--diff3",
                str(current_path),
                str(base_path),
                str(target_path),
            ],
            capture_output=True,
            check=False,
        )

    stdout = proc.stdout
    merged = stdout if isinstance(stdout, bytes) else str(stdout).encode("utf-8")
    if proc.returncode == 0:
        status = "CLEAN"
    elif 1 <= proc.returncode <= 127:
        # git merge-file returns the number of conflicts on conflict,
        # truncated to 127. Any positive value in that range is therefore a
        # valid analyzed conflict result rather than an execution failure.
        status = "CONFLICT"
    else:
        raise ValueError(
            "three-way merge analysis failed "
            f"(git merge-file return code {proc.returncode})"
        )

    return {
        "status": status,
        "conflict_markers": merged.count(b"<<<<<<<"),
        "candidate_git_blob": (
            _git_blob_sha_bytes(merged)
            if status == "CLEAN"
            else None
        ),
        "candidate_sha256": (
            _sha256_bytes(merged)
            if status == "CLEAN"
            else None
        ),
        "candidate_size": len(merged) if status == "CLEAN" else None,
        "candidate_bytes": merged if status == "CLEAN" else None,
    }


def _analyze_file(
    *,
    path: str,
    base: bytes,
    current: bytes,
    target: bytes,
    expected_base_blob: str,
    expected_target_blob: str,
    merge_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> dict[str, Any]:
    base_blob = _git_blob_sha_bytes(base)
    current_blob = _git_blob_sha_bytes(current)
    target_blob = _git_blob_sha_bytes(target)

    if base_blob != expected_base_blob:
        raise ValueError(f"production-base blob mismatch: {path}")
    if target_blob != expected_target_blob:
        raise ValueError(f"reviewed target blob mismatch: {path}")

    local_patch = _unified_patch(
        from_name=f"base/{path}",
        to_name=f"production-local/{path}",
        before=base,
        after=current,
    )
    reviewed_patch = _unified_patch(
        from_name=f"base/{path}",
        to_name=f"reviewed-target/{path}",
        before=base,
        after=target,
    )
    merge = _three_way_merge(
        current=current,
        base=base,
        target=target,
        runner=merge_runner,
    )

    candidate_blob = merge["candidate_git_blob"]
    candidate_bytes = merge.pop("candidate_bytes")
    candidate_patch = None
    if candidate_bytes is not None:
        candidate_patch = _unified_patch(
            from_name=f"production-local/{path}",
            to_name=f"merged-candidate/{path}",
            before=current,
            after=candidate_bytes,
        )

    return {
        "path": path,
        "expected_base_blob": expected_base_blob,
        "expected_target_blob": expected_target_blob,
        "base_blob": base_blob,
        "current_blob": current_blob,
        "current_sha256": _sha256_bytes(current),
        "current_size": len(current),
        "target_blob": target_blob,
        "target_sha256": _sha256_bytes(target),
        "target_size": len(target),
        "local_patch": _patch_stats(local_patch),
        "reviewed_patch": _patch_stats(reviewed_patch),
        "merge_status": merge["status"],
        "merge_conflict_markers": merge["conflict_markers"],
        "candidate_git_blob": candidate_blob,
        "candidate_sha256": merge["candidate_sha256"],
        "candidate_size": merge["candidate_size"],
        "candidate_equals_current": (
            candidate_blob == current_blob
            if candidate_blob is not None
            else False
        ),
        "candidate_equals_target": (
            candidate_blob == target_blob
            if candidate_blob is not None
            else False
        ),
        "candidate_patch_from_current": (
            _patch_stats(candidate_patch)
            if candidate_patch is not None
            else None
        ),
        "candidate_content_emitted": False,
    }


def validate_reconciliation_evidence(evidence: dict[str, Any]) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("reconciliation evidence must be a JSON object")

    expected_keys = set(IDENTITY_FIELDS) | {"evidence_sha256"}
    if set(evidence) != expected_keys:
        raise ValueError("reconciliation evidence fields do not match reviewed schema")
    if evidence.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported reconciliation evidence format")
    if evidence.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected reconciliation evidence artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if evidence.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("reconciliation evidence source lineage mismatch")

    if evidence.get("expected_production_head") != EXPECTED_PRODUCTION_HEAD:
        raise ValueError("reconciliation expected production head mismatch")
    if evidence.get("production_head") != EXPECTED_PRODUCTION_HEAD:
        raise ValueError("reconciliation production head is not the reviewed baseline")

    files = evidence.get("files")
    if not isinstance(files, list) or len(files) != 2:
        raise ValueError("reconciliation evidence must contain exactly two files")
    expected_paths = [RESEARCH_STORE, STATE_READER]
    if [item.get("path") for item in files] != expected_paths:
        raise ValueError("reconciliation evidence file order/scope is unexpected")

    for item in files:
        for field in (
            "expected_base_blob",
            "expected_target_blob",
            "base_blob",
            "current_blob",
            "target_blob",
        ):
            if not _is_hex_digest(item.get(field), 40):
                raise ValueError(f"reconciliation {field} is invalid: {item['path']}")
        for field in ("current_sha256", "target_sha256"):
            if not _is_hex_digest(item.get(field), 64):
                raise ValueError(f"reconciliation {field} is invalid: {item['path']}")
        if item["base_blob"] != item["expected_base_blob"]:
            raise ValueError(f"reconciliation base blob mismatch: {item['path']}")
        if item["target_blob"] != item["expected_target_blob"]:
            raise ValueError(f"reconciliation target blob mismatch: {item['path']}")
        if item.get("candidate_content_emitted") is not False:
            raise ValueError("reconciliation must not emit candidate source content")

        merge_status = item.get("merge_status")
        if merge_status not in {"CLEAN", "CONFLICT"}:
            raise ValueError("reconciliation merge status is invalid")
        if merge_status == "CLEAN":
            for field, length in (
                ("candidate_git_blob", 40),
                ("candidate_sha256", 64),
            ):
                if not _is_hex_digest(item.get(field), length):
                    raise ValueError(f"reconciliation clean candidate {field} is invalid")
            if item.get("candidate_patch_from_current") is None:
                raise ValueError("reconciliation clean candidate patch metadata is missing")
        else:
            if item.get("candidate_git_blob") is not None:
                raise ValueError("reconciliation conflicted candidate blob must be null")
            if item.get("candidate_sha256") is not None:
                raise ValueError("reconciliation conflicted candidate sha must be null")
            if item.get("candidate_size") is not None:
                raise ValueError("reconciliation conflicted candidate size must be null")
            if item.get("candidate_patch_from_current") is not None:
                raise ValueError("reconciliation conflicted candidate patch must be null")

    expected_all_clean = all(item["merge_status"] == "CLEAN" for item in files)
    if evidence.get("all_merge_clean") is not expected_all_clean:
        raise ValueError("reconciliation all-merge-clean flag mismatch")
    if evidence.get("candidate_content_emitted") is not False:
        raise ValueError("reconciliation evidence must not emit candidate content")
    if evidence.get("requires_manual_candidate_review") is not True:
        raise ValueError("reconciliation must require manual candidate review")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if evidence.get(field) is not False:
            raise ValueError(f"reconciliation requires {field}=false")

    if not _is_hex_digest(evidence.get("evidence_sha256"), 64):
        raise ValueError("reconciliation evidence digest is invalid")
    identity = {field: evidence[field] for field in IDENTITY_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if evidence["evidence_sha256"] != expected_digest:
        raise ValueError("reconciliation evidence digest mismatch")


def build_reconciliation_evidence(
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
    production_head = _production_head(repo, runner=runner)
    if production_head != EXPECTED_PRODUCTION_HEAD:
        raise ValueError(
            "production HEAD changed from reviewed baseline: "
            f"{production_head}"
        )

    manifest = json.loads(
        (source / PHASE2_MANIFEST).read_text(encoding="utf-8")
    )
    state_module = _load_module(
        source / STATE_READER_TOOL,
        "manual_market_paper_conflict_reconciliation_state_reader",
    )

    research_base = str(
        manifest["deployment_base_file_blobs"][RESEARCH_STORE]
    )
    research_target = str(
        manifest["deployment_target_file_blobs"][RESEARCH_STORE]
    )
    state_base = str(state_module.STACK_BASE_BLOB_SHA)
    state_target = str(state_module.STACK_TARGET_BLOB_SHA)

    specs = (
        (RESEARCH_STORE, research_base, research_target),
        (STATE_READER, state_base, state_target),
    )

    files = []
    for path, base_blob, target_blob in specs:
        production_path = repo / path
        target_path = source / path
        if production_path.is_symlink() or not production_path.is_file():
            raise ValueError(f"production conflict target is not a regular file: {path}")
        if target_path.is_symlink() or not target_path.is_file():
            raise ValueError(f"reviewed target is not a regular file: {path}")

        base_bytes = _git_show_bytes(
            repo,
            production_head,
            path,
            runner=runner,
        )
        files.append(
            _analyze_file(
                path=path,
                base=base_bytes,
                current=production_path.read_bytes(),
                target=target_path.read_bytes(),
                expected_base_blob=base_blob,
                expected_target_blob=target_blob,
                merge_runner=runner,
            )
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
        "production_head": production_head,
        "expected_production_head": EXPECTED_PRODUCTION_HEAD,
        "files": files,
        "all_merge_clean": all(item["merge_status"] == "CLEAN" for item in files),
        "candidate_content_emitted": False,
        "requires_manual_candidate_review": True,
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
    validate_reconciliation_evidence(evidence)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze the two current manual market/PAPER production-local "
            "conflicts with a read-only three-way merge: reviewed production "
            "base vs current production bytes vs reviewed target bytes. Temp "
            "files are used only for git merge-file analysis. No /opt/pio file "
            "is modified and no candidate source content is emitted."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    args = parser.parse_args()

    evidence = build_reconciliation_evidence(
        repository=args.repo,
        source_tree=args.source_tree,
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
