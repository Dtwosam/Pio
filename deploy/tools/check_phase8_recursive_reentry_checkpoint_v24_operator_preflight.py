from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V24_OPERATOR_PREFLIGHT_V1"
)
MANIFEST = Path(
    "deploy/manifests/phase8-recursive-reentry-checkpoint-v24-continuation.json"
)
MANIFEST_BLOB = "0d1ee4a65aaf44907fe9ccd6dff2ebcc3e3ce413"

EXPECTED_ROLES = (
    "canonical-checkpoint",
    "continuation-readiness",
    "tick-request",
    "detached-authorization",
    "execution-readiness",
    "one-shot-paper-executor",
    "post-execution-audit",
    "evidence-bundle",
)
EXPECTED_SAFETY_BOUNDARY = {
    "automatic_paper_execution_authorized": False,
    "recurring_paper_collection_authorized": False,
    "scheduler_execution_authorized": False,
    "live_submit_authorized": False,
    "transaction_submission_authorized": False,
    "new_live_capital_authorized": False,
    "continuous_promotion_authorized": False,
    "phase8_promotion_authorized": False,
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_manifest_blob",
    "manifest_sha256",
    "source_tree",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "allowed_signers_path",
    "allowed_signers_sha256",
    "ssh_keygen_path",
    "reviewed_tool_blobs",
    "manifest_valid",
    "reviewed_source_tools_verified",
    "source_tree_stable_during_preflight",
    "trust_root_verified",
    "ssh_signature_verifier_available",
    "database_stable_during_preflight",
    "one_shot_executor_only_mutating_step",
    "external_human_signature_required",
    "preflight_ready",
    "paper_supervisor_tick_authorized",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "continuous_promotion_authorized",
    "phase8_promotion_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git_blob_sha_bytes(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _git_blob_sha(path: Path) -> str:
    return _git_blob_sha_bytes(path.read_bytes())


def _is_hex_digest(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _regular_file(path: str | Path, *, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = resolved.stat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"unsafe Pio database state file: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _load_operator_manifest(source: Path) -> tuple[dict[str, Any], str]:
    path = source / MANIFEST
    if path.is_symlink() or not path.is_file():
        raise ValueError("checkpoint v24 operator manifest is missing")
    if _git_blob_sha(path) != MANIFEST_BLOB:
        raise ValueError("checkpoint v24 operator manifest blob mismatch")
    payload = path.read_bytes()
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("checkpoint v24 operator manifest must be an object")
    _validate_operator_manifest(value)
    return value, _sha256_bytes(payload)


def _validate_operator_manifest(value: dict[str, Any]) -> None:
    if value.get("format_version") != 1:
        raise ValueError("checkpoint v24 operator manifest format mismatch")
    if value.get("artifact_type") != (
        "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V24_CONTINUATION_OPERATOR_MANIFEST_V1"
    ):
        raise ValueError("checkpoint v24 operator manifest type mismatch")
    if value.get("checkpoint_version") != 24:
        raise ValueError("checkpoint v24 operator manifest version mismatch")
    steps = value.get("ordered_steps")
    if not isinstance(steps, list) or len(steps) != len(EXPECTED_ROLES):
        raise ValueError("checkpoint v24 operator manifest steps are invalid")
    if [item.get("order") for item in steps] != list(range(1, 9)):
        raise ValueError("checkpoint v24 operator manifest step order mismatch")
    if [item.get("role") for item in steps] != list(EXPECTED_ROLES):
        raise ValueError("checkpoint v24 operator manifest roles mismatch")

    mutating = [item for item in steps if item.get("paper_state_mutation") is True]
    if len(mutating) != 1 or mutating[0].get("role") != "one-shot-paper-executor":
        raise ValueError("checkpoint v24 operator manifest mutation boundary mismatch")
    if mutating[0].get("maximum_tick_count") != 1:
        raise ValueError("checkpoint v24 operator manifest tick bound mismatch")

    signed = next(
        item for item in steps
        if item.get("role") == "detached-authorization"
    )
    if signed.get("human_signature_required") is not True:
        raise ValueError("checkpoint v24 operator manifest signature boundary mismatch")

    boundary = value.get("execution_boundary")
    if not isinstance(boundary, dict):
        raise ValueError("checkpoint v24 operator manifest execution boundary missing")
    if boundary.get("only_mutating_step") != "one-shot-paper-executor":
        raise ValueError("checkpoint v24 operator manifest mutating step mismatch")
    if boundary.get("maximum_paper_ticks_per_authorization") != 1:
        raise ValueError("checkpoint v24 operator manifest authorization scope mismatch")
    for field in (
        "fresh_execution_readiness_required",
        "post_tick_audit_required",
        "final_bundle_requires_database_to_match_post_audit",
    ):
        if boundary.get(field) is not True:
            raise ValueError(
                f"checkpoint v24 operator manifest requires {field}=true"
            )

    if value.get("safety_boundary") != EXPECTED_SAFETY_BOUNDARY:
        raise ValueError("checkpoint v24 operator manifest safety boundary mismatch")


def _verify_source_tools(
    source: Path,
    manifest: dict[str, Any],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for step in manifest["ordered_steps"]:
        raw = step.get("tool")
        expected = step.get("git_blob")
        if (
            not isinstance(raw, str)
            or not raw
            or not _is_hex_digest(expected, 40)
        ):
            raise ValueError("checkpoint v24 operator manifest tool binding invalid")
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("checkpoint v24 operator manifest tool path unsafe")
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"checkpoint v24 reviewed tool missing: {relative}"
            )
        actual = _git_blob_sha(path)
        if actual != expected:
            raise ValueError(
                f"checkpoint v24 reviewed tool blob mismatch: {relative}"
            )
        result[relative.as_posix()] = actual
    return result


def _ssh_keygen_path() -> Path:
    raw = shutil.which("ssh-keygen")
    if raw is None:
        raise ValueError("ssh-keygen is not available")
    path = Path(raw).resolve(strict=True)
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or not (st.st_mode & stat.S_IXUSR):
        raise ValueError("ssh-keygen executable is invalid")
    return path


def validate_phase8_recursive_reentry_checkpoint_v24_operator_preflight(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("checkpoint v24 operator preflight must be an object")
    if set(report) != set(REPORT_FIELDS) | {"preflight_sha256"}:
        raise ValueError("checkpoint v24 operator preflight schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported checkpoint v24 operator preflight format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected checkpoint v24 operator preflight type")
    if report.get("reviewed_manifest_blob") != MANIFEST_BLOB:
        raise ValueError("checkpoint v24 operator preflight manifest lineage mismatch")

    for field in (
        "manifest_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "allowed_signers_sha256",
        "preflight_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"checkpoint v24 operator preflight {field} is invalid"
            )
    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"checkpoint v24 operator preflight {field} is invalid"
            )
    for field in (
        "source_tree",
        "production_repository",
        "pio_database_path",
        "allowed_signers_path",
        "ssh_keygen_path",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"checkpoint v24 operator preflight {field} is invalid"
            )

    tool_blobs = report.get("reviewed_tool_blobs")
    if not isinstance(tool_blobs, dict) or len(tool_blobs) != 8:
        raise ValueError("checkpoint v24 operator preflight tool blobs are invalid")
    for value in tool_blobs.values():
        if not _is_hex_digest(value, 40):
            raise ValueError(
                "checkpoint v24 operator preflight tool blob is invalid"
            )

    for field in (
        "manifest_valid",
        "reviewed_source_tools_verified",
        "source_tree_stable_during_preflight",
        "trust_root_verified",
        "ssh_signature_verifier_available",
        "database_stable_during_preflight",
        "one_shot_executor_only_mutating_step",
        "external_human_signature_required",
        "preflight_ready",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"checkpoint v24 operator preflight requires {field}=true"
            )

    for field in (
        "paper_supervisor_tick_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"checkpoint v24 operator preflight requires {field}=false"
            )

    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError("checkpoint v24 operator preflight database changed")
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError("checkpoint v24 operator preflight WAL changed")
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError("checkpoint v24 operator preflight SHM changed")

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["preflight_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError("checkpoint v24 operator preflight digest mismatch")


def build_phase8_recursive_reentry_checkpoint_v24_operator_preflight(
    *,
    repository: str | Path,
    source_tree: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")
    if not _is_hex_digest(expected_allowed_signers_sha256, 64):
        raise ValueError("expected allowed_signers SHA-256 is invalid")

    manifest, manifest_sha = _load_operator_manifest(source)
    tools_before = _verify_source_tools(source, manifest)

    allowed = _regular_file(
        allowed_signers_path,
        label="allowed_signers trust-root file",
    )
    allowed_sha = _sha256_bytes(allowed.read_bytes())
    if allowed_sha != expected_allowed_signers_sha256:
        raise ValueError("allowed_signers trust-root digest mismatch")

    ssh_keygen = _ssh_keygen_path()

    database = (production / "data" / "pio.db").resolve(strict=True)
    before = _database_state(database)
    if before["database"] is None:
        raise ValueError("production Pio database is missing")

    tools_after = _verify_source_tools(source, manifest)
    if tools_after != tools_before:
        raise ValueError("reviewed source tools changed during operator preflight")

    after = _database_state(database)
    if after != before:
        raise ValueError("production Pio database changed during operator preflight")

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_manifest_blob": MANIFEST_BLOB,
        "manifest_sha256": manifest_sha,
        "source_tree": str(source),
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "allowed_signers_path": str(allowed),
        "allowed_signers_sha256": allowed_sha,
        "ssh_keygen_path": str(ssh_keygen),
        "reviewed_tool_blobs": tools_before,
        "manifest_valid": True,
        "reviewed_source_tools_verified": True,
        "source_tree_stable_during_preflight": True,
        "trust_root_verified": True,
        "ssh_signature_verifier_available": True,
        "database_stable_during_preflight": True,
        "one_shot_executor_only_mutating_step": True,
        "external_human_signature_required": True,
        "preflight_ready": True,
        "paper_supervisor_tick_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "preflight_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_recursive_reentry_checkpoint_v24_operator_preflight(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only preflight for the checkpoint v24 continuation operator "
            "sequence. It verifies the reviewed manifest/tool blobs, production "
            "database stability, allowed_signers trust root and ssh-keygen. "
            "It does not sign, execute a PAPER tick, submit transactions or "
            "authorize any next action."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    args = parser.parse_args()

    report = build_phase8_recursive_reentry_checkpoint_v24_operator_preflight(
        repository=args.repo,
        source_tree=args.source_tree,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
