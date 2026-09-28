from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PHASE5_EVIDENCE_COLLECTION_READINESS_V1"

PHASE5_STATUS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_evidence_status.py"
)
COLLECTION_PLAN_TOOL = Path(
    "deploy/tools/build_manual_market_paper_phase5_evidence_collection_plan.py"
)
COLLECTION_REQUEST_TOOL = Path(
    "deploy/tools/build_manual_market_paper_phase5_evidence_collection_request.py"
)
SIGNED_AUTH_TOOL = Path(
    "deploy/tools/build_manual_market_paper_phase5_evidence_collection_signed_authorization.py"
)
PAPER_SERVICE_UNIT = Path("deploy/systemd/pio-paper@.service")
PAPER_TIMER_UNIT = Path("deploy/systemd/pio-paper@.timer")

REVIEWED_SOURCE_BLOBS = {
    PHASE5_STATUS_TOOL: "f3b90f3100c23f8454172f3bb97482e31df5921d",
    COLLECTION_PLAN_TOOL: "bef3d59207630528becf362915e148f06b3cb03a",
    COLLECTION_REQUEST_TOOL: "a210f74f1228679b7db338e44d7789825a9ecc8d",
    SIGNED_AUTH_TOOL: "d7e3cea9c402b06c8f3a1e7a2f2425fc75946760",
    PAPER_SERVICE_UNIT: "185526471bd8e6424837d5e3cec04341125202d7",
    PAPER_TIMER_UNIT: "5ed5dfd9aa99cf2b1f4169be277b19a9c6ffe5d5",
}

ENVIRONMENT_FILE = Path("/etc/pio/pio.env")
ENVIRONMENT_KEY = "PIO_PAPER_SCHEDULER_EXTRA_ARGS"
SYSTEMCTL_TIMEOUT_SECONDS = 10
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

UNIT_FIELDS = (
    "unit",
    "load_state",
    "active_state",
    "unit_file_state",
    "fragment_path",
    "fragment_sha256",
    "fragment_git_blob",
    "drop_in_paths",
    "fragment_matches_reviewed",
    "no_drop_ins",
)

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "phase5_evidence_status_sha256",
    "collection_plan_sha256",
    "collection_request_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
    "account",
    "run_id",
    "requested_collection_seconds",
    "scheduler_extra_args_text",
    "environment_file",
    "environment_file_sha256",
    "environment_scheduler_extra_args",
    "service_unit",
    "timer_unit",
    "fresh_phase5_status_matches_plan",
    "collection_request_matches_plan",
    "fresh_signed_authorization_matches_saved",
    "environment_args_match_request",
    "service_unit_matches_reviewed",
    "timer_unit_matches_reviewed",
    "service_inactive",
    "timer_inactive",
    "timer_disabled",
    "no_unit_drop_ins",
    "collection_readiness_ready",
    "automatic_stop_required",
    "requires_bounded_activation_executor",
    "collection_execution_authorized",
    "new_market_entry_authorized",
    "phase5_promotion_authorized",
    "recurring_paper_automation_authorized",
    "paper_timer_enable_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_paper_database_modified_by_readiness",
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


def _load_reviewed_modules(source: Path) -> tuple[Any, Any, Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"collection-readiness dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"collection-readiness dependency mismatch: {relative}")
    return (
        _load_module(
            source / PHASE5_STATUS_TOOL,
            "phase5_collection_readiness_status",
        ),
        _load_module(
            source / COLLECTION_PLAN_TOOL,
            "phase5_collection_readiness_plan",
        ),
        _load_module(
            source / COLLECTION_REQUEST_TOOL,
            "phase5_collection_readiness_request",
        ),
        _load_module(
            source / SIGNED_AUTH_TOOL,
            "phase5_collection_readiness_signed_auth",
        ),
    )


def _regular_no_follow(path: Path, *, label: str) -> bytes:
    try:
        st = os.lstat(path)
    except FileNotFoundError as exc:
        raise ValueError(f"{label} is missing") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    return path.read_bytes()


def _parse_environment_scheduler_args(path: Path) -> tuple[str, str]:
    payload = _regular_no_follow(path, label="PAPER environment file")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("PAPER environment file must be UTF-8") from exc

    values: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key.strip() != ENVIRONMENT_KEY:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if any(ch in value for ch in ("\n", "\r", "\x00")):
            raise ValueError("PAPER scheduler extra args contain unsafe characters")
        values.append(value)

    if len(values) != 1:
        raise ValueError(
            "PAPER environment must contain exactly one scheduler extra-args assignment"
        )
    return values[0], _sha256_bytes(payload)


def _systemctl_path() -> Path:
    raw = shutil.which("systemctl")
    if raw is None:
        raise ValueError("systemctl is unavailable")
    path = Path(raw).resolve(strict=True)
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or not (st.st_mode & stat.S_IXUSR):
        raise ValueError("systemctl executable is invalid")
    return path


def _systemctl_property(unit: str, prop: str) -> str:
    completed = subprocess.run(
        [
            str(_systemctl_path()),
            "show",
            unit,
            f"--property={prop}",
            "--value",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=SYSTEMCTL_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise ValueError(f"systemctl show failed for {unit} {prop}")
    return completed.stdout.strip()


def _unit_snapshot(
    *,
    source: Path,
    unit: str,
    reviewed_relative: Path,
) -> dict[str, Any]:
    load_state = _systemctl_property(unit, "LoadState")
    active_state = _systemctl_property(unit, "ActiveState")
    unit_file_state = _systemctl_property(unit, "UnitFileState")
    fragment_path_text = _systemctl_property(unit, "FragmentPath")
    drop_in_paths = _systemctl_property(unit, "DropInPaths")

    if load_state != "loaded":
        raise ValueError(f"{unit} is not loaded")
    if not fragment_path_text:
        raise ValueError(f"{unit} has no fragment path")

    fragment_path = Path(fragment_path_text)
    fragment_bytes = _regular_no_follow(
        fragment_path,
        label=f"{unit} fragment",
    )
    reviewed_bytes = (source / reviewed_relative).read_bytes()
    fragment_matches = fragment_bytes == reviewed_bytes
    no_drop_ins = drop_in_paths == ""

    return {
        "unit": unit,
        "load_state": load_state,
        "active_state": active_state,
        "unit_file_state": unit_file_state,
        "fragment_path": str(fragment_path),
        "fragment_sha256": _sha256_bytes(fragment_bytes),
        "fragment_git_blob": _git_blob_sha_bytes(fragment_bytes),
        "drop_in_paths": drop_in_paths,
        "fragment_matches_reviewed": fragment_matches,
        "no_drop_ins": no_drop_ins,
    }


def _validate_unit_snapshot(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or set(value) != set(UNIT_FIELDS):
        raise ValueError("collection-readiness unit snapshot schema mismatch")
    for field in ("unit", "load_state", "active_state", "unit_file_state", "fragment_path"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ValueError(f"collection-readiness unit {field} invalid")
    if not _is_hex_digest(value.get("fragment_sha256"), 64):
        raise ValueError("collection-readiness unit fragment SHA-256 invalid")
    if not _is_hex_digest(value.get("fragment_git_blob"), 40):
        raise ValueError("collection-readiness unit fragment blob invalid")
    if not isinstance(value.get("drop_in_paths"), str):
        raise ValueError("collection-readiness unit drop-in state invalid")
    for field in ("fragment_matches_reviewed", "no_drop_ins"):
        if not isinstance(value.get(field), bool):
            raise ValueError(f"collection-readiness unit {field} invalid")


def validate_collection_readiness(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("collection readiness must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"collection_readiness_sha256"}:
        raise ValueError("collection readiness schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported collection readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected collection readiness artifact type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("collection readiness source lineage mismatch")

    for field in (
        "phase5_evidence_status_sha256",
        "collection_plan_sha256",
        "collection_request_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "environment_file_sha256",
        "collection_readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"collection readiness {field} invalid")

    for field in (
        "account",
        "run_id",
        "scheduler_extra_args_text",
        "environment_file",
        "environment_scheduler_extra_args",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"collection readiness {field} invalid")

    seconds = report.get("requested_collection_seconds")
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        raise ValueError("collection readiness duration invalid")

    _validate_unit_snapshot(report.get("service_unit"))
    _validate_unit_snapshot(report.get("timer_unit"))

    for field in (
        "fresh_phase5_status_matches_plan",
        "collection_request_matches_plan",
        "fresh_signed_authorization_matches_saved",
        "environment_args_match_request",
        "service_unit_matches_reviewed",
        "timer_unit_matches_reviewed",
        "service_inactive",
        "timer_inactive",
        "timer_disabled",
        "no_unit_drop_ins",
        "collection_readiness_ready",
        "automatic_stop_required",
        "requires_bounded_activation_executor",
    ):
        if report.get(field) is not True:
            raise ValueError(f"collection readiness requires {field}=true")

    if report["environment_scheduler_extra_args"] != report["scheduler_extra_args_text"]:
        raise ValueError("collection readiness environment args mismatch")
    if report["service_unit"]["active_state"] != "inactive":
        raise ValueError("collection readiness PAPER service must be inactive")
    if report["timer_unit"]["active_state"] != "inactive":
        raise ValueError("collection readiness PAPER timer must be inactive")
    if report["timer_unit"]["unit_file_state"] != "disabled":
        raise ValueError("collection readiness PAPER timer must be disabled")

    for field in (
        "collection_execution_authorized",
        "new_market_entry_authorized",
        "phase5_promotion_authorized",
        "recurring_paper_automation_authorized",
        "paper_timer_enable_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_paper_database_modified_by_readiness",
    ):
        if report.get(field) is not False:
            raise ValueError(f"collection readiness requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["collection_readiness_sha256"] != expected:
        raise ValueError("collection readiness digest mismatch")


def build_collection_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    post_cycle_audit_path: str | Path,
    collection_plan_path: str | Path,
    collection_request_path: str | Path,
    saved_signed_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    environment_file: str | Path = ENVIRONMENT_FILE,
    now: str | None = None,
) -> dict[str, Any]:
    production_candidate = Path(repository).expanduser()
    source_candidate = Path(source_tree).expanduser()
    if production_candidate.is_symlink():
        raise ValueError("production repository root must not be a symlink")
    if source_candidate.is_symlink():
        raise ValueError("reviewed source tree must not be a symlink")
    production = production_candidate.resolve()
    source = source_candidate.resolve()
    if not production.is_dir():
        raise ValueError("production repository root is invalid")
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    status_module, plan_module, request_module, signed_module = (
        _load_reviewed_modules(source)
    )

    plan = _load_json(collection_plan_path, label="Phase 5 collection plan")
    request = _load_json(
        collection_request_path,
        label="Phase 5 collection request",
    )
    saved_verification = _load_json(
        saved_signed_verification_path,
        label="saved Phase 5 collection signed verification",
    )
    plan_module.validate_phase5_evidence_collection_plan(plan)
    request_module.validate_phase5_evidence_collection_request(request)
    signed_module.validate_verification(saved_verification)

    if request["collection_plan_sha256"] != plan["collection_plan_sha256"]:
        raise ValueError("collection request does not bind the supplied plan")
    if saved_verification["request_sha256"] != request["request_sha256"]:
        raise ValueError("signed verification does not bind the supplied request")

    fresh_status = status_module.build_phase5_evidence_status(
        repository=production,
        source_tree=source,
        post_cycle_audit_path=post_cycle_audit_path,
    )
    status_module.validate_phase5_evidence_status(fresh_status)
    if (
        fresh_status["phase5_evidence_status_sha256"]
        != plan["phase5_evidence_status_sha256"]
    ):
        raise ValueError("fresh Phase 5 status differs from the signed collection plan")

    fresh_verification = signed_module.verify_authorization(
        source_tree=source,
        request_path=collection_request_path,
        payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=expected_allowed_signers_sha256,
        now=now,
    )
    signed_module.validate_verification(fresh_verification)
    if fresh_verification != saved_verification:
        raise ValueError("fresh collection signature verification differs from saved")

    account = str(request["account"])
    if ACCOUNT_RE.fullmatch(account) is None:
        raise ValueError("collection readiness account is unsafe for systemd instance")

    environment_path = Path(environment_file)
    if environment_path.resolve(strict=False) != ENVIRONMENT_FILE:
        raise ValueError("collection readiness environment file binding mismatch")
    environment_args, environment_sha256 = _parse_environment_scheduler_args(
        environment_path
    )
    if environment_args != request["scheduler_extra_args_text"]:
        raise ValueError("production scheduler extra args differ from signed request")

    service_name = f"pio-paper@{account}.service"
    timer_name = f"pio-paper@{account}.timer"
    service = _unit_snapshot(
        source=source,
        unit=service_name,
        reviewed_relative=PAPER_SERVICE_UNIT,
    )
    timer = _unit_snapshot(
        source=source,
        unit=timer_name,
        reviewed_relative=PAPER_TIMER_UNIT,
    )

    service_matches = bool(
        service["fragment_matches_reviewed"]
        and service["fragment_git_blob"] == REVIEWED_SOURCE_BLOBS[PAPER_SERVICE_UNIT]
    )
    timer_matches = bool(
        timer["fragment_matches_reviewed"]
        and timer["fragment_git_blob"] == REVIEWED_SOURCE_BLOBS[PAPER_TIMER_UNIT]
    )
    service_inactive = service["active_state"] == "inactive"
    timer_inactive = timer["active_state"] == "inactive"
    timer_disabled = timer["unit_file_state"] == "disabled"
    no_drop_ins = bool(service["no_drop_ins"] and timer["no_drop_ins"])

    ready = bool(
        service_matches
        and timer_matches
        and service_inactive
        and timer_inactive
        and timer_disabled
        and no_drop_ins
    )
    if not ready:
        raise ValueError("Phase 5 evidence collection readiness failed closed")

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
        "phase5_evidence_status_sha256": fresh_status[
            "phase5_evidence_status_sha256"
        ],
        "collection_plan_sha256": plan["collection_plan_sha256"],
        "collection_request_sha256": request["request_sha256"],
        "saved_signed_authorization_verification_sha256": saved_verification[
            "verification_sha256"
        ],
        "fresh_signed_authorization_verification_sha256": fresh_verification[
            "verification_sha256"
        ],
        "account": account,
        "run_id": request["run_id"],
        "requested_collection_seconds": request[
            "requested_collection_seconds"
        ],
        "scheduler_extra_args_text": request["scheduler_extra_args_text"],
        "environment_file": str(ENVIRONMENT_FILE),
        "environment_file_sha256": environment_sha256,
        "environment_scheduler_extra_args": environment_args,
        "service_unit": service,
        "timer_unit": timer,
        "fresh_phase5_status_matches_plan": True,
        "collection_request_matches_plan": True,
        "fresh_signed_authorization_matches_saved": True,
        "environment_args_match_request": True,
        "service_unit_matches_reviewed": True,
        "timer_unit_matches_reviewed": True,
        "service_inactive": True,
        "timer_inactive": True,
        "timer_disabled": True,
        "no_unit_drop_ins": True,
        "collection_readiness_ready": True,
        "automatic_stop_required": True,
        "requires_bounded_activation_executor": True,
        "collection_execution_authorized": False,
        "new_market_entry_authorized": False,
        "phase5_promotion_authorized": False,
        "recurring_paper_automation_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_paper_database_modified_by_readiness": False,
    }
    report = {
        **identity,
        "collection_readiness_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_collection_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Perform the final read-only production readiness check before a "
            "bounded Phase 5 PAPER evidence-collection activation. The tool "
            "rebuilds current Phase 5 status, re-verifies the signed request, "
            "checks exact systemd fragments/drop-ins/state and the fixed "
            "/etc/pio/pio.env scheduler args, then stops before any timer or "
            "service action."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--post-cycle-audit", required=True)
    parser.add_argument("--collection-plan", required=True)
    parser.add_argument("--collection-request", required=True)
    parser.add_argument("--signed-authorization-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_collection_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        post_cycle_audit_path=args.post_cycle_audit,
        collection_plan_path=args.collection_plan,
        collection_request_path=args.collection_request,
        saved_signed_verification_path=args.signed_authorization_verification,
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
