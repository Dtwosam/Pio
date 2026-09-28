from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_ONE_CYCLE_EXECUTION_GATE_V1"

READINESS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_manual_cycle_readiness.py"
)
REQUEST_TOOL = Path(
    "deploy/tools/build_manual_market_paper_manual_cycle_authorization_request.py"
)
SIGNED_AUTHORIZATION_TOOL = Path(
    "deploy/tools/build_manual_market_paper_manual_cycle_signed_authorization.py"
)

REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "1e75644f9b4ea33738e8c4e7fddbdc4d08c98826",
    REQUEST_TOOL: "9a967b85585f46cf552e43d71afa948153b59546",
    SIGNED_AUTHORIZATION_TOOL: "9ff655267a8ac51573858d7cd37e451bb25dc7da",
}

AUTHORIZATION_SCOPE = "ONE_BOUNDED_MANUAL_MARKET_PAPER_CYCLE"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_manual_cycle_readiness_sha256",
    "fresh_manual_cycle_readiness_sha256",
    "authorization_request_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
    "production_repository",
    "approver_principal",
    "approval_id",
    "approval_expires_at",
    "authorization_scope",
    "account",
    "run_id",
    "cycle_parameters",
    "cycle_parameters_sha256",
    "fresh_readiness_matches_saved",
    "request_binds_readiness",
    "fresh_signed_authorization_matches_saved",
    "signed_authorization_binds_request",
    "human_cycle_authorization_verified",
    "approval_not_expired",
    "one_cycle_only",
    "paper_only",
    "manual_cycle_execution_gate_ready",
    "requires_immediate_one_shot_executor_recheck",
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


def _load_reviewed_modules(source: Path) -> tuple[Any, Any, Any]:
    modules: dict[Path, Any] = {}
    for index, (relative, expected_blob) in enumerate(
        sorted(REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0]))
    ):
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"one-cycle execution-gate dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"one-cycle execution-gate dependency mismatch: {relative}"
            )
        modules[relative] = _load_module(
            path,
            f"manual_market_paper_one_cycle_execution_gate_{index}",
        )
    return (
        modules[READINESS_TOOL],
        modules[REQUEST_TOOL],
        modules[SIGNED_AUTHORIZATION_TOOL],
    )


def validate_execution_gate(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("one-cycle execution gate must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"execution_gate_sha256"}:
        raise ValueError("one-cycle execution gate schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported one-cycle execution gate format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected one-cycle execution gate type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("one-cycle execution gate lineage mismatch")

    for field in (
        "saved_manual_cycle_readiness_sha256",
        "fresh_manual_cycle_readiness_sha256",
        "authorization_request_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "cycle_parameters_sha256",
        "execution_gate_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"one-cycle execution gate {field} is invalid")

    repository = report.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("one-cycle execution gate repository is invalid")
    for field in (
        "approver_principal",
        "approval_id",
        "approval_expires_at",
        "account",
        "run_id",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"one-cycle execution gate {field} is invalid")
    if report.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("one-cycle execution gate scope mismatch")

    parameters = report.get("cycle_parameters")
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError("one-cycle execution gate parameters are invalid")
    expected_parameters_sha = hashlib.sha256(
        _canonical_bytes(parameters)
    ).hexdigest()
    if report.get("cycle_parameters_sha256") != expected_parameters_sha:
        raise ValueError("one-cycle execution gate parameter digest mismatch")
    if parameters.get("account") != report["account"]:
        raise ValueError("one-cycle execution gate account binding mismatch")
    if parameters.get("run_id") != report["run_id"]:
        raise ValueError("one-cycle execution gate run-id binding mismatch")

    for field in (
        "fresh_readiness_matches_saved",
        "request_binds_readiness",
        "fresh_signed_authorization_matches_saved",
        "signed_authorization_binds_request",
        "human_cycle_authorization_verified",
        "approval_not_expired",
        "one_cycle_only",
        "paper_only",
        "manual_cycle_execution_gate_ready",
        "requires_immediate_one_shot_executor_recheck",
    ):
        if report.get(field) is not True:
            raise ValueError(f"one-cycle execution gate requires {field}=true")

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
            raise ValueError(f"one-cycle execution gate requires {field}=false")

    if report["saved_manual_cycle_readiness_sha256"] != report[
        "fresh_manual_cycle_readiness_sha256"
    ]:
        raise ValueError("one-cycle execution gate readiness digest mismatch")
    if report["saved_signed_authorization_verification_sha256"] != report[
        "fresh_signed_authorization_verification_sha256"
    ]:
        raise ValueError("one-cycle execution gate signed-verification digest mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["execution_gate_sha256"] != expected_digest:
        raise ValueError("one-cycle execution gate digest mismatch")


def build_execution_gate(
    *,
    repository: str | Path,
    source_tree: str | Path,
    private_bundle_report_path: str | Path,
    pre_mutation_handoff_path: str | Path,
    execution_precheck_path: str | Path,
    mutation_receipt_path: str | Path,
    post_mutation_audit_path: str | Path,
    manual_cycle_readiness_path: str | Path,
    authorization_request_path: str | Path,
    signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
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

    readiness_module, request_module, signed_module = _load_reviewed_modules(source)

    saved_readiness = _load_json(
        manual_cycle_readiness_path,
        label="saved manual-cycle readiness",
    )
    request = _load_json(
        authorization_request_path,
        label="one-cycle authorization request",
    )
    saved_signed = _load_json(
        signed_authorization_verification_path,
        label="saved one-cycle signed authorization verification",
    )

    readiness_module.validate_manual_cycle_readiness(saved_readiness)
    request_module.validate_manual_cycle_authorization_request(request)
    signed_module.validate_verification(saved_signed)

    if saved_readiness.get("manual_cycle_readiness_ready") is not True:
        raise ValueError("saved manual-cycle readiness is not ready")
    if request.get("authorization_request_ready") is not True:
        raise ValueError("one-cycle authorization request is not ready")
    if saved_signed.get("human_cycle_authorization_verified") is not True:
        raise ValueError("saved one-cycle human authorization is not verified")

    if request.get("manual_cycle_readiness_sha256") != saved_readiness.get(
        "manual_cycle_readiness_sha256"
    ):
        raise ValueError("one-cycle request/readiness binding mismatch")
    if request.get("production_repository") != saved_readiness.get(
        "production_repository"
    ):
        raise ValueError("one-cycle request repository binding mismatch")
    if Path(str(saved_readiness["production_repository"])).resolve() != production:
        raise ValueError("one-cycle execution gate production binding mismatch")

    if saved_signed.get("request_sha256") != request.get("request_sha256"):
        raise ValueError("one-cycle signed request binding mismatch")
    if saved_signed.get("manual_cycle_readiness_sha256") != saved_readiness.get(
        "manual_cycle_readiness_sha256"
    ):
        raise ValueError("one-cycle signed readiness binding mismatch")
    if saved_signed.get("cycle_parameters_sha256") != request.get(
        "cycle_parameters_sha256"
    ):
        raise ValueError("one-cycle signed parameter binding mismatch")
    if saved_signed.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("one-cycle signed authorization scope mismatch")
    if saved_signed.get("manual_cycle_execution_authorized") is not False:
        raise ValueError("saved one-cycle authorization must remain non-executing")

    fresh_readiness = readiness_module.build_manual_cycle_readiness(
        repository=production,
        source_tree=source,
        private_bundle_report_path=private_bundle_report_path,
        pre_mutation_handoff_path=pre_mutation_handoff_path,
        execution_precheck_path=execution_precheck_path,
        mutation_receipt_path=mutation_receipt_path,
        post_mutation_audit_path=post_mutation_audit_path,
    )
    readiness_module.validate_manual_cycle_readiness(fresh_readiness)
    if fresh_readiness != saved_readiness:
        raise ValueError(
            "one-cycle execution gate fresh readiness differs from saved readiness"
        )

    fresh_signed = signed_module.verify_authorization(
        source_tree=source,
        request_path=authorization_request_path,
        payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=expected_allowed_signers_sha256,
    )
    signed_module.validate_verification(fresh_signed)
    if fresh_signed != saved_signed:
        raise ValueError(
            "one-cycle execution gate fresh signed authorization differs from saved verification"
        )

    parameters = request["cycle_parameters"]
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
        "saved_manual_cycle_readiness_sha256": saved_readiness[
            "manual_cycle_readiness_sha256"
        ],
        "fresh_manual_cycle_readiness_sha256": fresh_readiness[
            "manual_cycle_readiness_sha256"
        ],
        "authorization_request_sha256": request["request_sha256"],
        "saved_signed_authorization_verification_sha256": saved_signed[
            "verification_sha256"
        ],
        "fresh_signed_authorization_verification_sha256": fresh_signed[
            "verification_sha256"
        ],
        "production_repository": str(production),
        "approver_principal": fresh_signed["approver_principal"],
        "approval_id": fresh_signed["approval_id"],
        "approval_expires_at": fresh_signed["expires_at"],
        "authorization_scope": AUTHORIZATION_SCOPE,
        "account": parameters["account"],
        "run_id": parameters["run_id"],
        "cycle_parameters": parameters,
        "cycle_parameters_sha256": request["cycle_parameters_sha256"],
        "fresh_readiness_matches_saved": True,
        "request_binds_readiness": True,
        "fresh_signed_authorization_matches_saved": True,
        "signed_authorization_binds_request": True,
        "human_cycle_authorization_verified": True,
        "approval_not_expired": True,
        "one_cycle_only": True,
        "paper_only": True,
        "manual_cycle_execution_gate_ready": True,
        "requires_immediate_one_shot_executor_recheck": True,
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
        "execution_gate_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_execution_gate(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build the final read-only gate before one bounded manual "
            "market/PAPER cycle. The gate re-runs current production readiness "
            "and re-verifies the short-lived detached human authorization, but "
            "does not execute the cycle or enable any recurring/live surface."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--private-bundle-report", required=True)
    parser.add_argument("--pre-mutation-handoff", required=True)
    parser.add_argument("--execution-precheck", required=True)
    parser.add_argument("--mutation-receipt", required=True)
    parser.add_argument("--post-mutation-audit", required=True)
    parser.add_argument("--manual-cycle-readiness", required=True)
    parser.add_argument("--authorization-request", required=True)
    parser.add_argument("--signed-authorization-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    args = parser.parse_args()

    report = build_execution_gate(
        repository=args.repo,
        source_tree=args.source_tree,
        private_bundle_report_path=args.private_bundle_report,
        pre_mutation_handoff_path=args.pre_mutation_handoff,
        execution_precheck_path=args.execution_precheck,
        mutation_receipt_path=args.mutation_receipt,
        post_mutation_audit_path=args.post_mutation_audit,
        manual_cycle_readiness_path=args.manual_cycle_readiness,
        authorization_request_path=args.authorization_request,
        signed_authorization_verification_path=args.signed_authorization_verification,
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
