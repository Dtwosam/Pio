from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_POST_MUTATION_AUDIT_V1"

WRITER_TOOL = Path(
    "deploy/tools/apply_manual_market_paper_preserved_file_mutations.py"
)
EXECUTION_PRECHECK_TOOL = Path(
    "deploy/tools/check_manual_market_paper_preserved_execution_precheck.py"
)
HANDOFF_TOOL = Path("deploy/tools/manual_market_paper_preserved_handoff.py")
READINESS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_preserved_readiness.py"
)
BACKUP_CAPTURE_TOOL = Path(
    "deploy/tools/capture_manual_market_paper_preserved_backups.py"
)

CREATE_FILE_MODE = 0o644

REVIEWED_SOURCE_BLOBS = {
    WRITER_TOOL: "04ab89cb673609daec60336b52bfdfe3f57239be",
    EXECUTION_PRECHECK_TOOL: "0e30043609b960e1fea9a2289bff777ce1d8290d",
    HANDOFF_TOOL: "3bc9c551a5bbe17bcedfc30e165a72e21966f407",
    READINESS_TOOL: "aec184548fa3149d42f006cd2b6dcb7d2da014f3",
    BACKUP_CAPTURE_TOOL: "6c69bdf05d6621d38a0db33e1b97e123c1f2e6ca",
}

TARGET_AUDIT_FIELDS = (
    "index",
    "layer",
    "operation",
    "path",
    "target_blob",
    "observed_blob",
    "target_mode",
    "observed_mode",
    "target_matches_receipt",
    "mode_matches_receipt",
    "rollback_required",
    "rollback_material_verified",
    "target_audit_ready",
)

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "mutation_receipt_sha256",
    "execution_precheck_sha256",
    "pre_mutation_handoff_state_sha256",
    "post_mutation_readiness_sha256",
    "production_repository",
    "approver_principal",
    "approval_id",
    "target_audits",
    "operation_count",
    "all_targets_verified",
    "all_rollback_material_verified",
    "production_head_unchanged",
    "detector_service_unchanged",
    "watcher_service_unchanged",
    "paper_service_unchanged",
    "paper_timer_unchanged",
    "target_pool_unchanged",
    "target_pool_cursor_unchanged",
    "runtime_files_deployed",
    "manual_mode_safe",
    "operational_services_healthy",
    "manual_paper_runtime_ready",
    "activation_state_unchanged",
    "activation_observed",
    "post_mutation_audit_ready",
    "production_file_modified",
    "production_repository_git_mutated",
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


def _load_json(path: str | Path, *, label: str) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = resolved.stat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_reviewed_modules(
    source: Path,
) -> tuple[Any, Any, Any, Any, Any]:
    modules: dict[Path, Any] = {}
    for index, (relative, expected_blob) in enumerate(
        sorted(REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0]))
    ):
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"reviewed post-mutation dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"reviewed post-mutation dependency mismatch: {relative}"
            )
        modules[relative] = _load_module(
            path,
            f"manual_market_paper_preserved_post_mutation_{index}",
        )
    return (
        modules[WRITER_TOOL],
        modules[EXECUTION_PRECHECK_TOOL],
        modules[HANDOFF_TOOL],
        modules[READINESS_TOOL],
        modules[BACKUP_CAPTURE_TOOL],
    )


def _backup_root(precheck: dict[str, Any]) -> Path:
    raw = precheck.get("backup_dir")
    if not isinstance(raw, str) or not raw.startswith("/var/tmp/"):
        raise ValueError("post-mutation backup directory is invalid")
    candidate = Path(raw)
    if candidate.is_symlink():
        raise ValueError("post-mutation backup directory must not be a symlink")
    resolved = candidate.resolve(strict=True)
    var_tmp = Path("/var/tmp").resolve()
    if var_tmp not in resolved.parents or not resolved.is_dir():
        raise ValueError("post-mutation backup directory escaped reviewed scope")
    return resolved


def _target_audit(
    *,
    result: dict[str, Any],
    precheck_operation: dict[str, Any],
    production: Path,
    backup_root: Path,
    backup_module: Any,
) -> dict[str, Any]:
    for field in ("index", "layer", "operation", "path", "target_blob"):
        if result.get(field) != precheck_operation.get(field):
            raise ValueError(
                f"post-mutation receipt/precheck binding mismatch: {field}"
            )
    if result.get("expected_current_blob") != precheck_operation.get(
        "expected_current_blob"
    ):
        raise ValueError(
            "post-mutation receipt/precheck expected-current mismatch"
        )
    if result.get("before_blob") != precheck_operation.get(
        "expected_current_blob"
    ):
        raise ValueError("post-mutation receipt before-state mismatch")

    expected_target_mode = (
        precheck_operation["backup_mode"]
        if precheck_operation["backup_required"]
        else CREATE_FILE_MODE
    )
    if result.get("target_mode") != expected_target_mode:
        raise ValueError("post-mutation receipt target-mode binding mismatch")

    relative = backup_module._safe_relative_path(result["path"])
    state, payload, mode = backup_module._read_regular_no_follow(
        production,
        relative,
    )
    if state != "BLOB_READ" or payload is None or mode is None:
        raise ValueError(
            f"post-mutation target is not a stable regular file: {result['path']}"
        )
    observed_blob = _git_blob_sha_bytes(payload)
    target_matches = bool(
        observed_blob == result["target_blob"] == result["after_blob"]
    )
    mode_matches = mode == result["target_mode"]
    if not target_matches:
        raise ValueError(f"post-mutation target blob mismatch: {result['path']}")
    if not mode_matches:
        raise ValueError(f"post-mutation target mode mismatch: {result['path']}")

    backup_required = bool(precheck_operation["backup_required"])
    if backup_required:
        backup_relative = backup_module._safe_backup_relative_path(
            precheck_operation["backup_relative_path"]
        )
        backup_state, backup_payload, backup_mode = (
            backup_module._read_regular_no_follow(
                backup_root,
                backup_relative,
            )
        )
        if (
            backup_state != "BLOB_READ"
            or backup_payload is None
            or backup_mode is None
        ):
            raise ValueError(
                f"post-mutation rollback material is unavailable: {result['path']}"
            )
        rollback_verified = bool(
            _git_blob_sha_bytes(backup_payload)
            == precheck_operation["backup_git_blob"]
            == precheck_operation["expected_current_blob"]
            and _sha256_bytes(backup_payload)
            == precheck_operation["backup_sha256"]
            and len(backup_payload) == precheck_operation["backup_size"]
            and backup_mode == precheck_operation["backup_mode"]
        )
        if not rollback_verified:
            raise ValueError(
                f"post-mutation rollback material mismatch: {result['path']}"
            )
    else:
        rollback_verified = True

    return {
        "index": result["index"],
        "layer": result["layer"],
        "operation": result["operation"],
        "path": result["path"],
        "target_blob": result["target_blob"],
        "observed_blob": observed_blob,
        "target_mode": result["target_mode"],
        "observed_mode": mode,
        "target_matches_receipt": target_matches,
        "mode_matches_receipt": mode_matches,
        "rollback_required": backup_required,
        "rollback_material_verified": rollback_verified,
        "target_audit_ready": bool(
            target_matches
            and mode_matches
            and rollback_verified
        ),
    }


def validate_post_mutation_audit(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("post-mutation audit must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"post_mutation_audit_sha256"}:
        raise ValueError("post-mutation audit fields do not match schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported post-mutation audit format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected post-mutation audit artifact type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("post-mutation audit source lineage mismatch")

    for field in (
        "mutation_receipt_sha256",
        "execution_precheck_sha256",
        "pre_mutation_handoff_state_sha256",
        "post_mutation_readiness_sha256",
        "post_mutation_audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"post-mutation audit {field} is invalid")

    repository = report.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("post-mutation audit repository is invalid")
    for field in ("approver_principal", "approval_id"):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"post-mutation audit {field} is invalid")

    audits = report.get("target_audits")
    if not isinstance(audits, list) or not audits:
        raise ValueError("post-mutation target audits must be non-empty")
    if report.get("operation_count") != len(audits):
        raise ValueError("post-mutation operation count mismatch")

    for expected_index, item in enumerate(audits):
        if not isinstance(item, dict) or set(item) != set(TARGET_AUDIT_FIELDS):
            raise ValueError("post-mutation target audit schema mismatch")
        if item.get("index") != expected_index:
            raise ValueError("post-mutation target audit indexes are not contiguous")
        for field in ("target_blob", "observed_blob"):
            if not _is_hex_digest(item.get(field), 40):
                raise ValueError(f"post-mutation target audit {field} is invalid")
        if item["target_blob"] != item["observed_blob"]:
            raise ValueError("post-mutation target audit blob mismatch")
        for field in ("target_mode", "observed_mode"):
            value = item.get(field)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or value > 0o7777
            ):
                raise ValueError(f"post-mutation target audit {field} is invalid")
        if item["target_mode"] != item["observed_mode"]:
            raise ValueError("post-mutation target audit mode mismatch")
        for field in (
            "target_matches_receipt",
            "mode_matches_receipt",
            "rollback_material_verified",
            "target_audit_ready",
        ):
            if item.get(field) is not True:
                raise ValueError(
                    f"post-mutation target audit requires {field}=true"
                )
        if not isinstance(item.get("rollback_required"), bool):
            raise ValueError("post-mutation rollback-required flag is invalid")

    for field in (
        "all_targets_verified",
        "all_rollback_material_verified",
        "production_head_unchanged",
        "detector_service_unchanged",
        "watcher_service_unchanged",
        "paper_service_unchanged",
        "paper_timer_unchanged",
        "target_pool_unchanged",
        "target_pool_cursor_unchanged",
        "runtime_files_deployed",
        "manual_mode_safe",
        "operational_services_healthy",
        "manual_paper_runtime_ready",
        "activation_state_unchanged",
        "post_mutation_audit_ready",
    ):
        if report.get(field) is not True:
            raise ValueError(f"post-mutation audit requires {field}=true")

    if report.get("activation_observed") is not False:
        raise ValueError("post-mutation audit must not observe activation")
    if report.get("production_file_modified") is not False:
        raise ValueError("post-mutation audit must be read-only")
    if report.get("production_repository_git_mutated") is not False:
        raise ValueError("post-mutation audit must not mutate production Git")
    for field in (
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if report.get(field) is not False:
            raise ValueError(f"post-mutation audit requires {field}=false")

    expected_targets = all(item["target_audit_ready"] for item in audits)
    expected_rollbacks = all(
        item["rollback_material_verified"] for item in audits
    )
    if report["all_targets_verified"] is not expected_targets:
        raise ValueError("post-mutation target aggregate mismatch")
    if report["all_rollback_material_verified"] is not expected_rollbacks:
        raise ValueError("post-mutation rollback aggregate mismatch")

    expected_activation_unchanged = all(
        report[field]
        for field in (
            "production_head_unchanged",
            "detector_service_unchanged",
            "watcher_service_unchanged",
            "paper_service_unchanged",
            "paper_timer_unchanged",
            "target_pool_unchanged",
            "target_pool_cursor_unchanged",
        )
    )
    if report["activation_state_unchanged"] is not expected_activation_unchanged:
        raise ValueError("post-mutation activation-state aggregate mismatch")

    expected_ready = bool(
        expected_targets
        and expected_rollbacks
        and expected_activation_unchanged
        and report["runtime_files_deployed"]
        and report["manual_mode_safe"]
        and report["operational_services_healthy"]
        and report["manual_paper_runtime_ready"]
        and report["activation_observed"] is False
    )
    if report["post_mutation_audit_ready"] is not expected_ready:
        raise ValueError("post-mutation audit ready flag mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["post_mutation_audit_sha256"] != expected_digest:
        raise ValueError("post-mutation audit digest mismatch")


def build_post_mutation_audit(
    *,
    repository: str | Path,
    source_tree: str | Path,
    private_bundle_report_path: str | Path,
    pre_mutation_handoff_path: str | Path,
    execution_precheck_path: str | Path,
    mutation_receipt_path: str | Path,
) -> dict[str, Any]:
    source_candidate = Path(source_tree).expanduser()
    production_candidate = Path(repository).expanduser()
    if source_candidate.is_symlink():
        raise ValueError("reviewed source tree must not be a symlink")
    if production_candidate.is_symlink():
        raise ValueError("production repository root must not be a symlink")
    source = source_candidate.resolve()
    production = production_candidate.resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    (
        writer_module,
        precheck_module,
        handoff_module,
        readiness_module,
        backup_module,
    ) = _load_reviewed_modules(source)

    receipt = _load_json(
        mutation_receipt_path,
        label="preserved file mutation receipt",
    )
    precheck = _load_json(
        execution_precheck_path,
        label="saved execution precheck",
    )
    handoff = _load_json(
        pre_mutation_handoff_path,
        label="pre-mutation preserved handoff",
    )

    writer_module.validate_mutation_receipt(receipt)
    precheck_module.validate_execution_precheck(precheck)
    handoff_module.validate_handoff_snapshot(handoff)

    if receipt.get("execution_precheck_sha256") != precheck.get(
        "execution_precheck_sha256"
    ):
        raise ValueError("post-mutation receipt/precheck digest binding mismatch")
    if Path(str(receipt["production_repository"])).resolve() != production:
        raise ValueError("post-mutation receipt repository binding mismatch")
    if Path(str(precheck["production_repository"])).resolve() != production:
        raise ValueError("post-mutation precheck repository binding mismatch")
    if handoff.get("handoff_ready") is not True:
        raise ValueError("pre-mutation preserved handoff is not ready")
    if receipt.get("file_mutation_completed") is not True:
        raise ValueError("preserved file mutation receipt is incomplete")

    receipt_results = receipt["operation_results"]
    precheck_operations = precheck["operation_prechecks"]
    if len(receipt_results) != len(precheck_operations):
        raise ValueError("post-mutation receipt/precheck operation count mismatch")

    backup_root = _backup_root(precheck)
    target_audits = [
        _target_audit(
            result=result,
            precheck_operation=precheck_operation,
            production=production,
            backup_root=backup_root,
            backup_module=backup_module,
        )
        for result, precheck_operation in zip(
            receipt_results,
            precheck_operations,
            strict=True,
        )
    ]

    prior_state = handoff["state"]
    fresh_readiness = readiness_module.build_preserved_readiness(
        repository=production,
        reviewed_source_tree=source,
        private_bundle_report_path=private_bundle_report_path,
        paper_account=prior_state["paper_account"],
        pool=prior_state["target_pool"],
    )
    readiness_module.validate_preserved_readiness(fresh_readiness)

    production_head_unchanged = (
        fresh_readiness["production_head"] == prior_state["production_head"]
    )
    detector_service_unchanged = (
        fresh_readiness["detector_service"] == prior_state["detector_service"]
    )
    watcher_service_unchanged = (
        fresh_readiness["watcher_service"] == prior_state["watcher_service"]
    )
    paper_service_unchanged = (
        fresh_readiness["paper_service"] == prior_state["paper_service"]
    )
    paper_timer_unchanged = (
        fresh_readiness["paper_timer"] == prior_state["paper_timer"]
    )
    target_pool_unchanged = (
        fresh_readiness["target_pool"] == prior_state["target_pool"]
    )
    target_pool_cursor_unchanged = (
        fresh_readiness["target_pool_cursor"]
        == prior_state["target_pool_cursor"]
    )

    activation_state_unchanged = all(
        (
            production_head_unchanged,
            detector_service_unchanged,
            watcher_service_unchanged,
            paper_service_unchanged,
            paper_timer_unchanged,
            target_pool_unchanged,
            target_pool_cursor_unchanged,
        )
    )
    all_targets_verified = all(
        item["target_audit_ready"] for item in target_audits
    )
    all_rollbacks_verified = all(
        item["rollback_material_verified"] for item in target_audits
    )
    runtime_files_deployed = bool(fresh_readiness["runtime_files_deployed"])
    manual_mode_safe = bool(fresh_readiness["manual_mode_safe"])
    operational_services_healthy = bool(
        fresh_readiness["operational_services_healthy"]
    )
    manual_paper_runtime_ready = bool(
        fresh_readiness["manual_paper_runtime_ready"]
    )
    activation_observed = not activation_state_unchanged

    audit_ready = bool(
        all_targets_verified
        and all_rollbacks_verified
        and activation_state_unchanged
        and runtime_files_deployed
        and manual_mode_safe
        and operational_services_healthy
        and manual_paper_runtime_ready
        and not activation_observed
    )
    if not audit_ready:
        raise ValueError("post-mutation audit failed closed")

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
        "mutation_receipt_sha256": receipt["receipt_sha256"],
        "execution_precheck_sha256": precheck["execution_precheck_sha256"],
        "pre_mutation_handoff_state_sha256": handoff[
            "production_state_sha256"
        ],
        "post_mutation_readiness_sha256": fresh_readiness["readiness_sha256"],
        "production_repository": str(production),
        "approver_principal": receipt["approver_principal"],
        "approval_id": receipt["approval_id"],
        "target_audits": target_audits,
        "operation_count": len(target_audits),
        "all_targets_verified": all_targets_verified,
        "all_rollback_material_verified": all_rollbacks_verified,
        "production_head_unchanged": production_head_unchanged,
        "detector_service_unchanged": detector_service_unchanged,
        "watcher_service_unchanged": watcher_service_unchanged,
        "paper_service_unchanged": paper_service_unchanged,
        "paper_timer_unchanged": paper_timer_unchanged,
        "target_pool_unchanged": target_pool_unchanged,
        "target_pool_cursor_unchanged": target_pool_cursor_unchanged,
        "runtime_files_deployed": runtime_files_deployed,
        "manual_mode_safe": manual_mode_safe,
        "operational_services_healthy": operational_services_healthy,
        "manual_paper_runtime_ready": manual_paper_runtime_ready,
        "activation_state_unchanged": activation_state_unchanged,
        "activation_observed": activation_observed,
        "post_mutation_audit_ready": audit_ready,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    report = {
        **identity,
        "post_mutation_audit_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_post_mutation_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Perform the read-only audit after an exact-scope preserved file "
            "mutation. The audit validates the writer receipt and execution "
            "precheck, re-reads every deployed target and rollback backup, runs "
            "fresh preserved readiness, and proves service/timer/cursor/HEAD "
            "activation state did not change. It never writes production."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--private-bundle-report", required=True)
    parser.add_argument("--pre-mutation-handoff", required=True)
    parser.add_argument("--execution-precheck", required=True)
    parser.add_argument("--mutation-receipt", required=True)
    args = parser.parse_args()

    report = build_post_mutation_audit(
        repository=args.repo,
        source_tree=args.source_tree,
        private_bundle_report_path=args.private_bundle_report,
        pre_mutation_handoff_path=args.pre_mutation_handoff,
        execution_precheck_path=args.execution_precheck,
        mutation_receipt_path=args.mutation_receipt,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
