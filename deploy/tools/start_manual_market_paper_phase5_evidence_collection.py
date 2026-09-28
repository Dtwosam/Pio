from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PHASE5_EVIDENCE_COLLECTION_ACTIVATION_RECEIPT_V1"

READINESS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_evidence_collection_readiness.py"
)
REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "6c72b5d63cf92eb48048f16e68f9258fa3be13dc",
}

LOCK_PATH = Path("/run/lock/pio-phase5-evidence-collection.lock")
COMMAND_TIMEOUT_SECONDS = 20
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

RECEIPT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "collection_readiness_sha256",
    "phase5_evidence_status_sha256",
    "collection_plan_sha256",
    "collection_request_sha256",
    "signed_authorization_verification_sha256",
    "account",
    "run_id",
    "target_timer_unit",
    "target_service_unit",
    "guard_timer_unit",
    "guard_service_unit",
    "requested_collection_seconds",
    "scheduler_extra_args_text",
    "started_at",
    "collection_deadline",
    "fresh_readiness_matches_saved",
    "runtime_lock_used",
    "auto_stop_guard_required",
    "auto_stop_guard_transient",
    "auto_stop_guard_scheduled",
    "reboot_fail_closed",
    "paper_timer_start_authorized",
    "paper_timer_started",
    "paper_timer_active",
    "paper_timer_unit_file_state",
    "paper_timer_remains_disabled",
    "bounded_collection_authorized",
    "bounded_collection_started",
    "paper_database_writes_expected_during_window",
    "requires_post_collection_audit",
    "new_market_entry_authorized",
    "phase5_promotion_authorized",
    "persistent_recurring_paper_automation_authorized",
    "paper_timer_enable_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "production_source_file_modified",
    "production_repository_git_mutated",
    "production_paper_database_modified_by_executor",
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
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_readiness_module(source: Path) -> Any:
    path = source / READINESS_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed collection readiness tool is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[READINESS_TOOL]:
        raise ValueError("reviewed collection readiness tool blob mismatch")
    return _load_module(
        path,
        "manual_market_paper_phase5_collection_activation_readiness",
    )


def _systemctl_path() -> Path:
    raw = shutil.which("systemctl")
    if raw is None:
        raise ValueError("systemctl is unavailable")
    path = Path(raw).resolve(strict=True)
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or not (st.st_mode & stat.S_IXUSR):
        raise ValueError("systemctl executable is invalid")
    return path


def _systemd_run_path() -> Path:
    raw = shutil.which("systemd-run")
    if raw is None:
        raise ValueError("systemd-run is unavailable")
    path = Path(raw).resolve(strict=True)
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or not (st.st_mode & stat.S_IXUSR):
        raise ValueError("systemd-run executable is invalid")
    return path


def _run(
    args: list[str],
    *,
    label: str,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise ValueError(f"{label} failed")
    return completed


def _systemctl_property(unit: str, prop: str) -> str:
    completed = _run(
        [
            str(_systemctl_path()),
            "show",
            unit,
            f"--property={prop}",
            "--value",
        ],
        label=f"systemctl show {unit} {prop}",
    )
    return completed.stdout.strip()


def _systemctl_start(unit: str) -> None:
    _run(
        [str(_systemctl_path()), "start", unit],
        label=f"systemctl start {unit}",
    )


def _systemctl_stop(*units: str) -> None:
    if not units:
        return
    try:
        _run(
            [str(_systemctl_path()), "stop", *units],
            label="systemctl stop cleanup",
        )
    except Exception:
        # Cleanup is best-effort only; the original failure remains primary.
        pass


def _schedule_stop_guard(
    *,
    guard_base: str,
    target_timer_unit: str,
    target_service_unit: str,
    seconds: int,
) -> tuple[str, str]:
    systemctl = _systemctl_path()
    systemd_run = _systemd_run_path()
    guard_service = f"{guard_base}.service"
    guard_timer = f"{guard_base}.timer"

    _run(
        [
            str(systemd_run),
            "--quiet",
            f"--unit={guard_base}",
            f"--on-active={seconds}s",
            "--timer-property=AccuracySec=1s",
            str(systemctl),
            "stop",
            target_timer_unit,
            target_service_unit,
        ],
        label="schedule Phase 5 collection auto-stop guard",
    )

    active_state = _systemctl_property(guard_timer, "ActiveState")
    if active_state != "active":
        _systemctl_stop(guard_timer, guard_service)
        raise ValueError("Phase 5 collection auto-stop guard is not active")
    return guard_timer, guard_service


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_activation_receipt(receipt: dict[str, Any]) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("Phase 5 collection activation receipt must be a JSON object")
    if set(receipt) != set(RECEIPT_FIELDS) | {"receipt_sha256"}:
        raise ValueError("Phase 5 collection activation receipt schema mismatch")
    if receipt.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 5 collection activation receipt format")
    if receipt.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 5 collection activation receipt type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if receipt.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 5 collection activation lineage mismatch")

    for field in (
        "collection_readiness_sha256",
        "phase5_evidence_status_sha256",
        "collection_plan_sha256",
        "collection_request_sha256",
        "signed_authorization_verification_sha256",
        "receipt_sha256",
    ):
        if not _is_hex_digest(receipt.get(field), 64):
            raise ValueError(f"Phase 5 collection activation {field} invalid")

    for field in (
        "account",
        "run_id",
        "target_timer_unit",
        "target_service_unit",
        "guard_timer_unit",
        "guard_service_unit",
        "scheduler_extra_args_text",
        "started_at",
        "collection_deadline",
        "paper_timer_unit_file_state",
    ):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ValueError(f"Phase 5 collection activation {field} invalid")

    account = receipt["account"]
    if ACCOUNT_RE.fullmatch(account) is None:
        raise ValueError("Phase 5 collection activation account invalid")
    if receipt["target_timer_unit"] != f"pio-paper@{account}.timer":
        raise ValueError("Phase 5 collection activation timer binding mismatch")
    if receipt["target_service_unit"] != f"pio-paper@{account}.service":
        raise ValueError("Phase 5 collection activation service binding mismatch")

    seconds = receipt.get("requested_collection_seconds")
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        raise ValueError("Phase 5 collection activation duration invalid")

    started = datetime.strptime(
        receipt["started_at"],
        "%Y-%m-%dT%H:%M:%SZ",
    ).replace(tzinfo=timezone.utc)
    deadline = datetime.strptime(
        receipt["collection_deadline"],
        "%Y-%m-%dT%H:%M:%SZ",
    ).replace(tzinfo=timezone.utc)
    if deadline - started != timedelta(seconds=seconds):
        raise ValueError("Phase 5 collection activation deadline mismatch")

    for field in (
        "fresh_readiness_matches_saved",
        "runtime_lock_used",
        "auto_stop_guard_required",
        "auto_stop_guard_transient",
        "auto_stop_guard_scheduled",
        "reboot_fail_closed",
        "paper_timer_start_authorized",
        "paper_timer_started",
        "paper_timer_active",
        "paper_timer_remains_disabled",
        "bounded_collection_authorized",
        "bounded_collection_started",
        "paper_database_writes_expected_during_window",
        "requires_post_collection_audit",
    ):
        if receipt.get(field) is not True:
            raise ValueError(f"Phase 5 collection activation requires {field}=true")

    if receipt.get("paper_timer_unit_file_state") != "disabled":
        raise ValueError("Phase 5 collection timer must remain disabled")

    for field in (
        "new_market_entry_authorized",
        "phase5_promotion_authorized",
        "persistent_recurring_paper_automation_authorized",
        "paper_timer_enable_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_paper_database_modified_by_executor",
    ):
        if receipt.get(field) is not False:
            raise ValueError(f"Phase 5 collection activation requires {field}=false")

    identity = {field: receipt[field] for field in RECEIPT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if receipt["receipt_sha256"] != expected:
        raise ValueError("Phase 5 collection activation receipt digest mismatch")


def activate_phase5_evidence_collection(
    *,
    repository: str | Path,
    source_tree: str | Path,
    post_cycle_audit_path: str | Path,
    collection_plan_path: str | Path,
    collection_request_path: str | Path,
    signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    collection_readiness_path: str | Path,
    expected_collection_readiness_sha256: str,
    lock_path: str | Path = LOCK_PATH,
) -> dict[str, Any]:
    if not _is_hex_digest(expected_collection_readiness_sha256, 64):
        raise ValueError("expected collection readiness SHA-256 is invalid")

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

    readiness_module = _load_readiness_module(source)
    saved = _load_json(
        collection_readiness_path,
        label="saved Phase 5 collection readiness",
    )
    readiness_module.validate_collection_readiness(saved)
    if saved["collection_readiness_sha256"] != expected_collection_readiness_sha256:
        raise ValueError("saved collection readiness does not match expected digest")
    if saved.get("collection_readiness_ready") is not True:
        raise ValueError("saved collection readiness is not ready")
    if saved.get("requires_bounded_activation_executor") is not True:
        raise ValueError("saved collection readiness does not require this executor")

    account = str(saved["account"])
    if ACCOUNT_RE.fullmatch(account) is None:
        raise ValueError("collection activation account is unsafe")
    target_timer = f"pio-paper@{account}.timer"
    target_service = f"pio-paper@{account}.service"

    lock = Path(lock_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Phase 5 collection activation lock is busy") from exc

        fresh = readiness_module.build_collection_readiness(
            repository=production,
            source_tree=source,
            post_cycle_audit_path=post_cycle_audit_path,
            collection_plan_path=collection_plan_path,
            collection_request_path=collection_request_path,
            saved_signed_verification_path=signed_authorization_verification_path,
            signed_payload_path=signed_payload_path,
            signature_path=signature_path,
            allowed_signers_path=allowed_signers_path,
            expected_allowed_signers_sha256=expected_allowed_signers_sha256,
        )
        readiness_module.validate_collection_readiness(fresh)
        if fresh != saved:
            raise ValueError("fresh collection readiness differs from saved readiness")

        seconds = int(saved["requested_collection_seconds"])
        readiness_sha = saved["collection_readiness_sha256"]
        guard_base = (
            f"pio-phase5-evidence-stop-{account}-{readiness_sha[:12]}"
        )
        guard_timer = ""
        guard_service = ""

        try:
            guard_timer, guard_service = _schedule_stop_guard(
                guard_base=guard_base,
                target_timer_unit=target_timer,
                target_service_unit=target_service,
                seconds=seconds,
            )
            _systemctl_start(target_timer)

            active_state = _systemctl_property(target_timer, "ActiveState")
            unit_file_state = _systemctl_property(
                target_timer,
                "UnitFileState",
            )
            if active_state != "active":
                raise ValueError("Phase 5 PAPER timer did not become active")
            if unit_file_state != "disabled":
                raise ValueError(
                    "Phase 5 PAPER timer became persistent instead of remaining disabled"
                )
        except Exception:
            _systemctl_stop(target_timer, target_service)
            if guard_timer or guard_service:
                _systemctl_stop(
                    *(unit for unit in (guard_timer, guard_service) if unit)
                )
            raise

        started = datetime.now(timezone.utc).replace(microsecond=0)
        deadline = started + timedelta(seconds=seconds)

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
            "collection_readiness_sha256": readiness_sha,
            "phase5_evidence_status_sha256": saved[
                "phase5_evidence_status_sha256"
            ],
            "collection_plan_sha256": saved["collection_plan_sha256"],
            "collection_request_sha256": saved["collection_request_sha256"],
            "signed_authorization_verification_sha256": saved[
                "saved_signed_authorization_verification_sha256"
            ],
            "account": account,
            "run_id": saved["run_id"],
            "target_timer_unit": target_timer,
            "target_service_unit": target_service,
            "guard_timer_unit": guard_timer,
            "guard_service_unit": guard_service,
            "requested_collection_seconds": seconds,
            "scheduler_extra_args_text": saved["scheduler_extra_args_text"],
            "started_at": _format_utc(started),
            "collection_deadline": _format_utc(deadline),
            "fresh_readiness_matches_saved": True,
            "runtime_lock_used": True,
            "auto_stop_guard_required": True,
            "auto_stop_guard_transient": True,
            "auto_stop_guard_scheduled": True,
            "reboot_fail_closed": True,
            "paper_timer_start_authorized": True,
            "paper_timer_started": True,
            "paper_timer_active": True,
            "paper_timer_unit_file_state": "disabled",
            "paper_timer_remains_disabled": True,
            "bounded_collection_authorized": True,
            "bounded_collection_started": True,
            "paper_database_writes_expected_during_window": True,
            "requires_post_collection_audit": True,
            "new_market_entry_authorized": False,
            "phase5_promotion_authorized": False,
            "persistent_recurring_paper_automation_authorized": False,
            "paper_timer_enable_authorized": False,
            "service_restart_authorized": False,
            "detector_cursor_movement_authorized": False,
            "transaction_signing_authorized": False,
            "transaction_submission_authorized": False,
            "live_capital_authorized": False,
            "production_source_file_modified": False,
            "production_repository_git_mutated": False,
            "production_paper_database_modified_by_executor": False,
        }
        receipt = {
            **identity,
            "receipt_sha256": _sha256_bytes(_canonical_bytes(identity)),
        }
        validate_activation_receipt(receipt)
        return receipt


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Start one explicitly authorized, bounded Phase 5 PAPER evidence "
            "collection window. The executor fresh-rechecks the exact saved "
            "readiness, schedules a transient stop guard first, then starts "
            "the already-disabled PAPER timer. It never enables the timer, "
            "creates new market entries, persists Phase 5 promotion, touches "
            "source/Git state, signs/submits transactions, or uses live capital."
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
    parser.add_argument("--collection-readiness", required=True)
    parser.add_argument("--expected-collection-readiness-sha256", required=True)
    args = parser.parse_args()

    receipt = activate_phase5_evidence_collection(
        repository=args.repo,
        source_tree=args.source_tree,
        post_cycle_audit_path=args.post_cycle_audit,
        collection_plan_path=args.collection_plan,
        collection_request_path=args.collection_request,
        signed_authorization_verification_path=(
            args.signed_authorization_verification
        ),
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
        collection_readiness_path=args.collection_readiness,
        expected_collection_readiness_sha256=(
            args.expected_collection_readiness_sha256
        ),
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
