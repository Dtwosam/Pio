from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE8_OFFLINE_STEP_REQUEST_V1"

HANDOFF_TOOL = Path(
    "deploy/tools/build_phase8_post_phase7_operator_handoff.py"
)
REVIEWED_SOURCE_BLOBS = {
    HANDOFF_TOOL: "60d7bee1b76739b8540a8a345c77dc30eea28a78",
}

ALLOWED_DEBT_TYPES = {
    "RETRAIN_DATASET_BUILD_READY",
    "RETRAIN_OFFLINE_TRAIN_READY",
    "RETRAIN_OFFLINE_VALIDATION_READY",
}
AUTHORIZATION_SCOPE = "EXECUTE_ONE_PHASE8_OFFLINE_RESEARCH_STEP_ONLY"

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "authorization_scope",
    "saved_phase8_handoff_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "phase8_status",
    "debt_type",
    "scope",
    "reason",
    "suggested_command",
    "automatic_action_available",
    "allowed_offline_debt_type",
    "one_step_only",
    "request_ready",
    "explicit_human_authorization_required",
    "fresh_phase8_handoff_recheck_required",
    "offline_step_authorization_present",
    "offline_step_execution_authorized",
    "offline_step_executed",
    "paper_challenger_transition_authorized",
    "paper_trading_authorized",
    "new_live_entry_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_capital_authorized",
    "phase8_policy_action_authorized",
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
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_handoff_module(source: Path) -> Any:
    path = source / HANDOFF_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed Phase 8 handoff tool is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[HANDOFF_TOOL]:
        raise ValueError("reviewed Phase 8 handoff blob mismatch")
    return _load_module(path, "phase8_offline_step_request_handoff")


def validate_phase8_offline_step_request(request: dict[str, Any]) -> None:
    if not isinstance(request, dict):
        raise ValueError("Phase 8 offline-step request must be a JSON object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("Phase 8 offline-step request schema mismatch")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 8 offline-step request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 8 offline-step request type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 8 offline-step request lineage mismatch")

    for field in (
        "saved_phase8_handoff_sha256",
        "pio_database_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(
                f"Phase 8 offline-step request {field} is invalid"
            )

    for field in (
        "production_repository",
        "pio_database_path",
        "phase8_status",
        "debt_type",
        "scope",
        "reason",
        "suggested_command",
    ):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError(
                f"Phase 8 offline-step request {field} is invalid"
            )

    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("Phase 8 offline-step request scope mismatch")
    if request.get("phase8_status") != "AUTOMATIC_ACTION":
        raise ValueError(
            "Phase 8 offline-step request requires AUTOMATIC_ACTION status"
        )
    if request.get("debt_type") not in ALLOWED_DEBT_TYPES:
        raise ValueError("Phase 8 offline-step debt type is not allowed")

    for field in (
        "automatic_action_available",
        "allowed_offline_debt_type",
        "one_step_only",
        "request_ready",
        "explicit_human_authorization_required",
        "fresh_phase8_handoff_recheck_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                f"Phase 8 offline-step request requires {field}=true"
            )

    for field in (
        "offline_step_authorization_present",
        "offline_step_execution_authorized",
        "offline_step_executed",
        "paper_challenger_transition_authorized",
        "paper_trading_authorized",
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "phase8_policy_action_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if request.get(field) is not False:
            raise ValueError(
                f"Phase 8 offline-step request requires {field}=false"
            )

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if request["request_sha256"] != expected:
        raise ValueError("Phase 8 offline-step request digest mismatch")


def build_phase8_offline_step_request(
    *,
    source_tree: str | Path,
    phase8_handoff_path: str | Path,
    expected_phase8_handoff_sha256: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    handoff_module = _load_handoff_module(source)
    handoff = _load_json(
        phase8_handoff_path,
        label="Phase 8 post-Phase 7 operator handoff",
    )
    handoff_module.validate_phase8_post_phase7_handoff(handoff)

    if (
        not _is_hex_digest(expected_phase8_handoff_sha256, 64)
        or handoff["handoff_sha256"] != expected_phase8_handoff_sha256
    ):
        raise ValueError("Phase 8 offline-step handoff digest mismatch")

    for field in (
        "phase7_promotion_confirmed",
        "phase7_promotion_persisted",
        "phase8_handoff_ready",
        "phase8_research_only",
        "phase8_read_only",
    ):
        if handoff.get(field) is not True:
            raise ValueError(
                f"Phase 8 offline-step request requires handoff {field}=true"
            )
    for field in (
        "phase8_policy_actionable",
        "phase8_execution_wired",
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "phase8_policy_action_authorized",
        "phase8_execution_authorized",
        "phase8_promotion_authorized",
        "production_pio_database_modified",
    ):
        if handoff.get(field) is not False:
            raise ValueError(
                f"Phase 8 offline-step request refuses handoff {field}=true"
            )

    operator = handoff.get("phase8_operator_handoff")
    if not isinstance(operator, dict):
        raise ValueError("Phase 8 operator handoff is missing")
    if operator.get("status") != "AUTOMATIC_ACTION":
        raise ValueError("Phase 8 next action is not automatic")
    if operator.get("automatic_action_available") is not True:
        raise ValueError("Phase 8 automatic action is unavailable")
    if operator.get("operator_action_required") is not False:
        raise ValueError("Phase 8 next action still requires an operator")
    if operator.get("manual_input_required") is not False:
        raise ValueError("Phase 8 next action still requires manual input")
    if (
        operator.get("policy_actionable") is not False
        or operator.get("execution_wired") is not False
        or operator.get("research_only") is not True
        or operator.get("read_only") is not True
    ):
        raise ValueError("Phase 8 operator handoff safety boundary changed")

    debt_type = operator.get("debt_type")
    if debt_type not in ALLOWED_DEBT_TYPES:
        raise ValueError("Phase 8 automatic debt type is not allowed")
    scope = operator.get("scope")
    reason = operator.get("reason")
    suggested = operator.get("suggested_command")
    for label, value in (
        ("scope", scope),
        ("reason", reason),
        ("suggested_command", suggested),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"Phase 8 automatic action {label} is invalid")

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
        "authorization_scope": AUTHORIZATION_SCOPE,
        "saved_phase8_handoff_sha256": handoff["handoff_sha256"],
        "production_repository": handoff["production_repository"],
        "pio_database_path": handoff["pio_database_path"],
        "pio_database_sha256": handoff["pio_database_sha256_after"],
        "phase8_status": "AUTOMATIC_ACTION",
        "debt_type": str(debt_type),
        "scope": str(scope),
        "reason": str(reason),
        "suggested_command": str(suggested),
        "automatic_action_available": True,
        "allowed_offline_debt_type": True,
        "one_step_only": True,
        "request_ready": True,
        "explicit_human_authorization_required": True,
        "fresh_phase8_handoff_recheck_required": True,
        "offline_step_authorization_present": False,
        "offline_step_execution_authorized": False,
        "offline_step_executed": False,
        "paper_challenger_transition_authorized": False,
        "paper_trading_authorized": False,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_policy_action_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    request = {
        **identity,
        "request_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_phase8_offline_step_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing request for exactly one automatic "
            "Phase 8 offline research step. The request never starts PAPER, "
            "submits transactions, uses live capital, or mutates production."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase8-handoff", required=True)
    parser.add_argument("--expected-phase8-handoff-sha256", required=True)
    args = parser.parse_args()

    report = build_phase8_offline_step_request(
        source_tree=args.source_tree,
        phase8_handoff_path=args.phase8_handoff,
        expected_phase8_handoff_sha256=(
            args.expected_phase8_handoff_sha256
        ),
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
