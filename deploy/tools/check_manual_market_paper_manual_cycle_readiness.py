from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_MANUAL_CYCLE_READINESS_V1"

POST_MUTATION_AUDIT_TOOL = Path(
    "deploy/tools/check_manual_market_paper_preserved_post_mutation.py"
)
RUNTIME_MANIFEST = Path("deploy/manifests/market-paper-runtime.json")
MANUAL_CYCLE_CLI = Path(
    "python-learner/src/meteora_learner/manual_market_paper_cycle_cli.py"
)

REVIEWED_SOURCE_BLOBS = {
    POST_MUTATION_AUDIT_TOOL: "f23bae2b423fe68778c75cb1131ed5822902d5d6",
    RUNTIME_MANIFEST: "5d4330b905aba20692bcd75ecf324cb8b1a0f684",
    MANUAL_CYCLE_CLI: "87175be0cf5398162c2af42d0a9475d694e02d6f",
}

EXPECTED_ACTIVATION_MODE = "MANUAL_ONLY"
EXPECTED_DEPLOYMENT_SCOPE = "MANUAL_MARKET_PAPER_RUNTIME"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_post_mutation_audit_sha256",
    "fresh_post_mutation_audit_sha256",
    "production_repository",
    "mutation_receipt_sha256",
    "execution_precheck_sha256",
    "approver_principal",
    "approval_id",
    "runtime_manifest_git_blob",
    "manual_cycle_cli_git_blob",
    "fresh_post_mutation_audit_matches_saved",
    "post_mutation_audit_ready",
    "runtime_files_deployed",
    "manual_paper_runtime_ready",
    "manual_mode_safe",
    "operational_services_healthy",
    "activation_state_unchanged",
    "activation_observed",
    "runtime_manifest_manual_only",
    "manual_cycle_cli_reviewed",
    "manual_cycle_readiness_ready",
    "requires_separate_manual_cycle_authorization",
    "manual_cycle_execution_authorized",
    "paper_timer_enable_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
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
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_reviewed_audit_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"manual-cycle readiness dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"manual-cycle readiness dependency mismatch: {relative}"
            )
    return _load_module(
        source / POST_MUTATION_AUDIT_TOOL,
        "manual_market_paper_manual_cycle_readiness_post_mutation",
    )


def _validate_runtime_manifest(source: Path) -> dict[str, Any]:
    manifest = _load_json(
        source / RUNTIME_MANIFEST,
        label="manual market/PAPER runtime manifest",
    )
    if manifest.get("format_version") != 1:
        raise ValueError("manual-cycle runtime manifest format mismatch")
    if manifest.get("deployment_scope") != EXPECTED_DEPLOYMENT_SCOPE:
        raise ValueError("manual-cycle runtime manifest scope mismatch")
    if manifest.get("activation_mode") != EXPECTED_ACTIVATION_MODE:
        raise ValueError("manual-cycle runtime manifest activation mode mismatch")
    if manifest.get("paper_only") is not True:
        raise ValueError("manual-cycle runtime manifest must remain PAPER-only")
    if manifest.get("deployment_guard_apply_locked") is not True:
        raise ValueError("manual-cycle runtime manifest apply guard must remain locked")

    for field in (
        "production_deployment_authorized",
        "detector_cursor_movement_authorized",
        "service_restart_authorized",
        "live_capital_authorized",
    ):
        if manifest.get(field) is not False:
            raise ValueError(
                f"manual-cycle runtime manifest requires {field}=false"
            )

    files = manifest.get("deployment_files")
    targets = manifest.get("deployment_target_file_blobs")
    cli_path = str(MANUAL_CYCLE_CLI)
    if not isinstance(files, list) or cli_path not in files:
        raise ValueError("manual-cycle CLI is absent from runtime manifest")
    if not isinstance(targets, dict):
        raise ValueError("manual-cycle runtime target map is invalid")
    if targets.get(cli_path) != REVIEWED_SOURCE_BLOBS[MANUAL_CYCLE_CLI]:
        raise ValueError("manual-cycle CLI target blob mismatch")
    return manifest


def validate_manual_cycle_readiness(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("manual-cycle readiness must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"manual_cycle_readiness_sha256"}:
        raise ValueError("manual-cycle readiness fields do not match schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported manual-cycle readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected manual-cycle readiness artifact type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("manual-cycle readiness source lineage mismatch")

    for field in (
        "saved_post_mutation_audit_sha256",
        "fresh_post_mutation_audit_sha256",
        "mutation_receipt_sha256",
        "execution_precheck_sha256",
        "manual_cycle_readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"manual-cycle readiness {field} is invalid")

    for field in ("runtime_manifest_git_blob", "manual_cycle_cli_git_blob"):
        if not _is_hex_digest(report.get(field), 40):
            raise ValueError(f"manual-cycle readiness {field} is invalid")

    repository = report.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("manual-cycle readiness production repository is invalid")
    for field in ("approver_principal", "approval_id"):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"manual-cycle readiness {field} is invalid")

    for field in (
        "fresh_post_mutation_audit_matches_saved",
        "post_mutation_audit_ready",
        "runtime_files_deployed",
        "manual_paper_runtime_ready",
        "manual_mode_safe",
        "operational_services_healthy",
        "activation_state_unchanged",
        "runtime_manifest_manual_only",
        "manual_cycle_cli_reviewed",
        "manual_cycle_readiness_ready",
        "requires_separate_manual_cycle_authorization",
    ):
        if report.get(field) is not True:
            raise ValueError(f"manual-cycle readiness requires {field}=true")

    if report.get("activation_observed") is not False:
        raise ValueError("manual-cycle readiness must not observe activation")

    for field in (
        "manual_cycle_execution_authorized",
        "paper_timer_enable_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
    ):
        if report.get(field) is not False:
            raise ValueError(f"manual-cycle readiness requires {field}=false")

    if report["saved_post_mutation_audit_sha256"] != report[
        "fresh_post_mutation_audit_sha256"
    ]:
        raise ValueError("manual-cycle readiness post-mutation audit digest mismatch")

    expected_ready = all(
        report[field]
        for field in (
            "fresh_post_mutation_audit_matches_saved",
            "post_mutation_audit_ready",
            "runtime_files_deployed",
            "manual_paper_runtime_ready",
            "manual_mode_safe",
            "operational_services_healthy",
            "activation_state_unchanged",
            "runtime_manifest_manual_only",
            "manual_cycle_cli_reviewed",
        )
    ) and report["activation_observed"] is False
    if report["manual_cycle_readiness_ready"] is not expected_ready:
        raise ValueError("manual-cycle readiness aggregate mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["manual_cycle_readiness_sha256"] != expected_digest:
        raise ValueError("manual-cycle readiness digest mismatch")


def build_manual_cycle_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    private_bundle_report_path: str | Path,
    pre_mutation_handoff_path: str | Path,
    execution_precheck_path: str | Path,
    mutation_receipt_path: str | Path,
    post_mutation_audit_path: str | Path,
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

    audit_module = _load_reviewed_audit_module(source)
    _validate_runtime_manifest(source)

    saved = _load_json(
        post_mutation_audit_path,
        label="saved preserved post-mutation audit",
    )
    audit_module.validate_post_mutation_audit(saved)
    if saved.get("post_mutation_audit_ready") is not True:
        raise ValueError("saved post-mutation audit is not ready")

    fresh = audit_module.build_post_mutation_audit(
        repository=production,
        source_tree=source,
        private_bundle_report_path=private_bundle_report_path,
        pre_mutation_handoff_path=pre_mutation_handoff_path,
        execution_precheck_path=execution_precheck_path,
        mutation_receipt_path=mutation_receipt_path,
    )
    audit_module.validate_post_mutation_audit(fresh)
    if fresh != saved:
        raise ValueError(
            "manual-cycle readiness fresh post-mutation audit differs from saved audit"
        )

    required_true = (
        "runtime_files_deployed",
        "manual_paper_runtime_ready",
        "manual_mode_safe",
        "operational_services_healthy",
        "activation_state_unchanged",
    )
    for field in required_true:
        if fresh.get(field) is not True:
            raise ValueError(
                f"manual-cycle readiness requires post-mutation {field}=true"
            )
    if fresh.get("activation_observed") is not False:
        raise ValueError("manual-cycle readiness requires activation_observed=false")

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
        "saved_post_mutation_audit_sha256": saved[
            "post_mutation_audit_sha256"
        ],
        "fresh_post_mutation_audit_sha256": fresh[
            "post_mutation_audit_sha256"
        ],
        "production_repository": fresh["production_repository"],
        "mutation_receipt_sha256": fresh["mutation_receipt_sha256"],
        "execution_precheck_sha256": fresh["execution_precheck_sha256"],
        "approver_principal": fresh["approver_principal"],
        "approval_id": fresh["approval_id"],
        "runtime_manifest_git_blob": REVIEWED_SOURCE_BLOBS[RUNTIME_MANIFEST],
        "manual_cycle_cli_git_blob": REVIEWED_SOURCE_BLOBS[MANUAL_CYCLE_CLI],
        "fresh_post_mutation_audit_matches_saved": True,
        "post_mutation_audit_ready": True,
        "runtime_files_deployed": True,
        "manual_paper_runtime_ready": True,
        "manual_mode_safe": True,
        "operational_services_healthy": True,
        "activation_state_unchanged": True,
        "activation_observed": False,
        "runtime_manifest_manual_only": True,
        "manual_cycle_cli_reviewed": True,
        "manual_cycle_readiness_ready": True,
        "requires_separate_manual_cycle_authorization": True,
        "manual_cycle_execution_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
    }
    report = {
        **identity,
        "manual_cycle_readiness_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_manual_cycle_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build the final read-only readiness artifact before one bounded "
            "manual market/PAPER cycle. The tool re-runs the complete "
            "post-mutation audit against current production and verifies the "
            "reviewed MANUAL_ONLY runtime/CLI lineage. It never starts a service, "
            "enables a timer, moves a cursor, signs/submits a transaction, or "
            "authorizes the manual cycle itself."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--private-bundle-report", required=True)
    parser.add_argument("--pre-mutation-handoff", required=True)
    parser.add_argument("--execution-precheck", required=True)
    parser.add_argument("--mutation-receipt", required=True)
    parser.add_argument("--post-mutation-audit", required=True)
    args = parser.parse_args()

    report = build_manual_cycle_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        private_bundle_report_path=args.private_bundle_report,
        pre_mutation_handoff_path=args.pre_mutation_handoff,
        execution_precheck_path=args.execution_precheck,
        mutation_receipt_path=args.mutation_receipt,
        post_mutation_audit_path=args.post_mutation_audit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
