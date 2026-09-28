from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PHASE5_EVIDENCE_COLLECTION_PLAN_V1"

PHASE5_STATUS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_evidence_status.py"
)
PAPER_SERVICE_UNIT = Path("deploy/systemd/pio-paper@.service")
PAPER_TIMER_UNIT = Path("deploy/systemd/pio-paper@.timer")
CLI_MODULE = Path("python-learner/src/meteora_learner/cli.py")
PAPER_SCHEDULER_MODULE = Path(
    "python-learner/src/meteora_learner/paper_scheduler.py"
)

REVIEWED_SOURCE_BLOBS = {
    PHASE5_STATUS_TOOL: "f3b90f3100c23f8454172f3bb97482e31df5921d",
    PAPER_SERVICE_UNIT: "185526471bd8e6424837d5e3cec04341125202d7",
    PAPER_TIMER_UNIT: "5ed5dfd9aa99cf2b1f4169be277b19a9c6ffe5d5",
    CLI_MODULE: "38fb5c01c9cd92a2befa68ee57530e280829307c",
    PAPER_SCHEDULER_MODULE: "52a658e5b0717451619a4cb17f125f595c5e1331",
}

SCHEDULER_INTERVAL_SECONDS = 300
SCHEDULER_LEASE_SECONDS = 900
TIMER_INTERVAL_MINUTES = 5

PLAN_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "phase5_evidence_status_sha256",
    "account",
    "run_id",
    "phase5_promotion_ready",
    "phase5_reasons",
    "phase5_criteria",
    "phase3_promoted",
    "ledger_passing",
    "current_evidence",
    "evidence_deficits",
    "health_checks",
    "minimum_nominal_collection_hours",
    "recurring_scheduler_evidence_needed",
    "entry_diversity_evidence_needed",
    "phase3_dependency_blocked",
    "historical_failure_streak_blocked",
    "timer_interval_seconds",
    "scheduler_lease_seconds",
    "scheduler_service_blob",
    "scheduler_timer_blob",
    "scheduler_cli_blob",
    "scheduler_module_blob",
    "scheduler_scope",
    "scheduler_can_collect",
    "scheduler_cannot_collect",
    "requires_exact_scheduler_extra_args_review",
    "collection_plan_ready",
    "requires_separate_collection_authorization",
    "requires_separate_manual_entry_authorization",
    "requires_separate_phase5_promotion_action",
    "phase5_promotion_persisted",
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
    "production_paper_database_modified",
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


def _load_status_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 5 collection-plan dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 5 collection-plan dependency mismatch: {relative}"
            )
    return _load_module(
        source / PHASE5_STATUS_TOOL,
        "manual_market_paper_phase5_collection_plan_status",
    )


def _nonnegative_number(value: Any, *, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(f"Phase 5 collection-plan {label} is invalid")
    return float(value)


def _nonnegative_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"Phase 5 collection-plan {label} is invalid")
    return value


def _derive(status: dict[str, Any]) -> dict[str, Any]:
    criteria = status["phase5_criteria"]
    endurance = status["endurance"]
    ledger = status["ledger_audit"]

    current = {
        "runtime_hours": _nonnegative_number(
            endurance.get("runtime_hours"),
            label="runtime_hours",
        ),
        "terminal_ticks": _nonnegative_int(
            endurance.get("terminal_ticks"),
            label="terminal_ticks",
        ),
        "success_rate_pct": _nonnegative_number(
            endurance.get("success_rate_pct"),
            label="success_rate_pct",
        ),
        "dependency_blocked_pct": _nonnegative_number(
            endurance.get("dependency_blocked_pct"),
            label="dependency_blocked_pct",
        ),
        "max_observed_consecutive_failures": _nonnegative_int(
            endurance.get("max_observed_consecutive_failures"),
            label="max_observed_consecutive_failures",
        ),
        "stale_running_ticks": _nonnegative_int(
            endurance.get("stale_running_ticks"),
            label="stale_running_ticks",
        ),
        "applied_chain_valuations": _nonnegative_int(
            endurance.get("applied_chain_valuations"),
            label="applied_chain_valuations",
        ),
        "distinct_positions_valued": _nonnegative_int(
            endurance.get("distinct_positions_valued"),
            label="distinct_positions_valued",
        ),
        "closed_positions": _nonnegative_int(
            status.get("closed_positions"),
            label="closed_positions",
        ),
        "distinct_valued_pools": _nonnegative_int(
            status.get("distinct_valued_pools"),
            label="distinct_valued_pools",
        ),
    }

    deficits = {
        "runtime_hours": max(
            0.0,
            float(criteria["min_runtime_hours"]) - current["runtime_hours"],
        ),
        "terminal_ticks": max(
            0,
            int(criteria["min_terminal_ticks"]) - current["terminal_ticks"],
        ),
        "applied_chain_valuations": max(
            0,
            int(criteria["min_applied_chain_valuations"])
            - current["applied_chain_valuations"],
        ),
        "distinct_positions_valued": max(
            0,
            int(criteria["min_distinct_positions_valued"])
            - current["distinct_positions_valued"],
        ),
        "closed_positions": max(
            0,
            int(criteria["min_closed_positions"]) - current["closed_positions"],
        ),
        "distinct_valued_pools": max(
            0,
            int(criteria["min_distinct_pools"])
            - current["distinct_valued_pools"],
        ),
    }

    health = {
        "success_rate_meets": (
            current["success_rate_pct"] >= float(criteria["min_success_rate_pct"])
        ),
        "dependency_blocked_meets": (
            current["dependency_blocked_pct"]
            <= float(criteria["max_dependency_blocked_pct"])
        ),
        "consecutive_failures_meet": (
            current["max_observed_consecutive_failures"]
            <= int(criteria["max_consecutive_failures"])
        ),
        "stale_running_ticks_meet": (
            current["stale_running_ticks"]
            <= int(criteria["max_stale_running_ticks"])
        ),
    }

    nominal_hours_for_ticks = (
        deficits["terminal_ticks"] * TIMER_INTERVAL_MINUTES / 60.0
    )
    minimum_nominal_collection_hours = max(
        deficits["runtime_hours"],
        nominal_hours_for_ticks,
    )

    recurring_scheduler_evidence_needed = bool(
        deficits["runtime_hours"]
        or deficits["terminal_ticks"]
        or deficits["applied_chain_valuations"]
        or not health["success_rate_meets"]
        or not health["dependency_blocked_meets"]
        or not health["stale_running_ticks_meet"]
    )
    entry_diversity_evidence_needed = bool(
        deficits["distinct_positions_valued"]
        or deficits["closed_positions"]
        or deficits["distinct_valued_pools"]
    )

    return {
        "current": current,
        "deficits": deficits,
        "health": health,
        "minimum_nominal_collection_hours": minimum_nominal_collection_hours,
        "recurring_scheduler_evidence_needed": recurring_scheduler_evidence_needed,
        "entry_diversity_evidence_needed": entry_diversity_evidence_needed,
        "historical_failure_streak_blocked": not health[
            "consecutive_failures_meet"
        ],
        "ledger_passing": ledger.get("passing") is True,
    }


def validate_phase5_evidence_collection_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        raise ValueError("Phase 5 evidence collection plan must be a JSON object")
    if set(plan) != set(PLAN_FIELDS) | {"collection_plan_sha256"}:
        raise ValueError("Phase 5 evidence collection plan schema mismatch")
    if plan.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 5 evidence collection plan format")
    if plan.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 5 evidence collection plan type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if plan.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 5 collection-plan source lineage mismatch")

    for field in (
        "phase5_evidence_status_sha256",
        "collection_plan_sha256",
    ):
        if not _is_hex_digest(plan.get(field), 64):
            raise ValueError(f"Phase 5 collection-plan {field} is invalid")
    for field in (
        "scheduler_service_blob",
        "scheduler_timer_blob",
        "scheduler_cli_blob",
        "scheduler_module_blob",
    ):
        if not _is_hex_digest(plan.get(field), 40):
            raise ValueError(f"Phase 5 collection-plan {field} is invalid")

    for field in ("account", "run_id"):
        if not isinstance(plan.get(field), str) or not plan[field]:
            raise ValueError(f"Phase 5 collection-plan {field} is invalid")

    if plan.get("scheduler_service_blob") != REVIEWED_SOURCE_BLOBS[
        PAPER_SERVICE_UNIT
    ]:
        raise ValueError("Phase 5 scheduler service blob mismatch")
    if plan.get("scheduler_timer_blob") != REVIEWED_SOURCE_BLOBS[PAPER_TIMER_UNIT]:
        raise ValueError("Phase 5 scheduler timer blob mismatch")
    if plan.get("scheduler_cli_blob") != REVIEWED_SOURCE_BLOBS[CLI_MODULE]:
        raise ValueError("Phase 5 scheduler CLI blob mismatch")
    if plan.get("scheduler_module_blob") != REVIEWED_SOURCE_BLOBS[
        PAPER_SCHEDULER_MODULE
    ]:
        raise ValueError("Phase 5 scheduler module blob mismatch")

    if plan.get("timer_interval_seconds") != SCHEDULER_INTERVAL_SECONDS:
        raise ValueError("Phase 5 timer interval mismatch")
    if plan.get("scheduler_lease_seconds") != SCHEDULER_LEASE_SECONDS:
        raise ValueError("Phase 5 scheduler lease mismatch")
    if plan.get("scheduler_scope") != "PAPER_EVIDENCE_COLLECTION_ONLY":
        raise ValueError("Phase 5 scheduler scope mismatch")

    if plan.get("scheduler_can_collect") != [
        "runtime_hours",
        "terminal_ticks",
        "success_rate",
        "dependency_blocked_rate",
        "applied_chain_valuations",
        "open_position_management",
    ]:
        raise ValueError("Phase 5 scheduler capability list mismatch")
    if plan.get("scheduler_cannot_collect") != [
        "new_market_entry_creation",
        "phase3_promotion",
        "phase5_promotion_persistence",
        "live_execution",
    ]:
        raise ValueError("Phase 5 scheduler exclusion list mismatch")

    if not isinstance(plan.get("phase5_promotion_ready"), bool):
        raise ValueError("Phase 5 collection-plan promotion-ready flag invalid")
    if not isinstance(plan.get("phase3_promoted"), bool):
        raise ValueError("Phase 5 collection-plan Phase 3 flag invalid")
    if not isinstance(plan.get("ledger_passing"), bool):
        raise ValueError("Phase 5 collection-plan ledger flag invalid")

    reasons = plan.get("phase5_reasons")
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ):
        raise ValueError("Phase 5 collection-plan reasons are invalid")
    if plan["phase5_promotion_ready"] is not (len(reasons) == 0):
        raise ValueError("Phase 5 collection-plan promotion/reasons mismatch")

    current = plan.get("current_evidence")
    deficits = plan.get("evidence_deficits")
    health = plan.get("health_checks")
    if not isinstance(current, dict) or not isinstance(deficits, dict):
        raise ValueError("Phase 5 collection-plan evidence blocks invalid")
    if not isinstance(health, dict):
        raise ValueError("Phase 5 collection-plan health block invalid")

    for field in (
        "runtime_hours",
        "success_rate_pct",
        "dependency_blocked_pct",
    ):
        _nonnegative_number(current.get(field), label=field)
    for field in (
        "terminal_ticks",
        "max_observed_consecutive_failures",
        "stale_running_ticks",
        "applied_chain_valuations",
        "distinct_positions_valued",
        "closed_positions",
        "distinct_valued_pools",
    ):
        _nonnegative_int(current.get(field), label=field)
    for field, value in deficits.items():
        if field == "runtime_hours":
            _nonnegative_number(value, label=f"deficit.{field}")
        else:
            _nonnegative_int(value, label=f"deficit.{field}")
    for field in (
        "success_rate_meets",
        "dependency_blocked_meets",
        "consecutive_failures_meet",
        "stale_running_ticks_meet",
    ):
        if not isinstance(health.get(field), bool):
            raise ValueError(f"Phase 5 collection-plan health {field} invalid")

    _nonnegative_number(
        plan.get("minimum_nominal_collection_hours"),
        label="minimum_nominal_collection_hours",
    )

    expected_phase3_blocked = not plan["phase3_promoted"]
    if plan.get("phase3_dependency_blocked") is not expected_phase3_blocked:
        raise ValueError("Phase 5 collection-plan Phase 3 dependency mismatch")
    expected_streak_blocked = not health["consecutive_failures_meet"]
    if plan.get("historical_failure_streak_blocked") is not expected_streak_blocked:
        raise ValueError("Phase 5 collection-plan failure-streak mismatch")

    for field in (
        "recurring_scheduler_evidence_needed",
        "entry_diversity_evidence_needed",
        "requires_exact_scheduler_extra_args_review",
        "collection_plan_ready",
        "requires_separate_collection_authorization",
        "requires_separate_manual_entry_authorization",
        "requires_separate_phase5_promotion_action",
    ):
        if not isinstance(plan.get(field), bool):
            raise ValueError(f"Phase 5 collection-plan {field} must be boolean")
    if plan.get("requires_exact_scheduler_extra_args_review") is not True:
        raise ValueError("Phase 5 collection-plan must review scheduler extra args")
    if plan.get("collection_plan_ready") is not True:
        raise ValueError("Phase 5 collection plan must be ready")
    if plan.get("requires_separate_collection_authorization") is not True:
        raise ValueError("Phase 5 collection plan requires separate authorization")
    if plan.get("requires_separate_manual_entry_authorization") is not True:
        raise ValueError("Phase 5 collection plan requires separate entry authorization")
    if plan.get("requires_separate_phase5_promotion_action") is not True:
        raise ValueError("Phase 5 collection plan requires separate promotion")

    for field in (
        "phase5_promotion_persisted",
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
        "production_paper_database_modified",
    ):
        if plan.get(field) is not False:
            raise ValueError(f"Phase 5 collection-plan requires {field}=false")

    identity = {field: plan[field] for field in PLAN_FIELDS}
    expected = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if plan["collection_plan_sha256"] != expected:
        raise ValueError("Phase 5 evidence collection plan digest mismatch")


def build_phase5_evidence_collection_plan(
    *,
    source_tree: str | Path,
    phase5_evidence_status_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    status_module = _load_status_module(source)
    status = _load_json(
        phase5_evidence_status_path,
        label="Phase 5 evidence status",
    )
    status_module.validate_phase5_evidence_status(status)
    if status.get("phase5_evidence_status_ready") is not True:
        raise ValueError("Phase 5 evidence status is not ready")
    for field in (
        "phase5_promotion_persisted",
        "phase5_promotion_authorized",
        "recurring_paper_automation_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if status.get(field) is not False:
            raise ValueError(
                f"Phase 5 evidence status unexpectedly authorizes {field}"
            )

    derived = _derive(status)

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
        "phase5_evidence_status_sha256": status[
            "phase5_evidence_status_sha256"
        ],
        "account": status["account"],
        "run_id": status["run_id"],
        "phase5_promotion_ready": bool(status["phase5_promotion_ready"]),
        "phase5_reasons": list(status["phase5_reasons"]),
        "phase5_criteria": status["phase5_criteria"],
        "phase3_promoted": bool(status["phase3_promoted"]),
        "ledger_passing": derived["ledger_passing"],
        "current_evidence": derived["current"],
        "evidence_deficits": derived["deficits"],
        "health_checks": derived["health"],
        "minimum_nominal_collection_hours": derived[
            "minimum_nominal_collection_hours"
        ],
        "recurring_scheduler_evidence_needed": derived[
            "recurring_scheduler_evidence_needed"
        ],
        "entry_diversity_evidence_needed": derived[
            "entry_diversity_evidence_needed"
        ],
        "phase3_dependency_blocked": not bool(status["phase3_promoted"]),
        "historical_failure_streak_blocked": derived[
            "historical_failure_streak_blocked"
        ],
        "timer_interval_seconds": SCHEDULER_INTERVAL_SECONDS,
        "scheduler_lease_seconds": SCHEDULER_LEASE_SECONDS,
        "scheduler_service_blob": REVIEWED_SOURCE_BLOBS[PAPER_SERVICE_UNIT],
        "scheduler_timer_blob": REVIEWED_SOURCE_BLOBS[PAPER_TIMER_UNIT],
        "scheduler_cli_blob": REVIEWED_SOURCE_BLOBS[CLI_MODULE],
        "scheduler_module_blob": REVIEWED_SOURCE_BLOBS[
            PAPER_SCHEDULER_MODULE
        ],
        "scheduler_scope": "PAPER_EVIDENCE_COLLECTION_ONLY",
        "scheduler_can_collect": [
            "runtime_hours",
            "terminal_ticks",
            "success_rate",
            "dependency_blocked_rate",
            "applied_chain_valuations",
            "open_position_management",
        ],
        "scheduler_cannot_collect": [
            "new_market_entry_creation",
            "phase3_promotion",
            "phase5_promotion_persistence",
            "live_execution",
        ],
        "requires_exact_scheduler_extra_args_review": True,
        "collection_plan_ready": True,
        "requires_separate_collection_authorization": True,
        "requires_separate_manual_entry_authorization": True,
        "requires_separate_phase5_promotion_action": True,
        "phase5_promotion_persisted": False,
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
        "production_paper_database_modified": False,
    }
    plan = {
        **identity,
        "collection_plan_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_phase5_evidence_collection_plan(plan)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Turn a reviewed Phase 5 PAPER evidence-status artifact into a "
            "sealed, read-only collection plan. The plan distinguishes evidence "
            "the five-minute PAPER scheduler can accumulate from entry/pool "
            "diversity evidence that still requires separately authorized "
            "bounded manual cycles. It does not enable the timer, persist "
            "promotion, execute PAPER, or authorize live capital."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase5-evidence-status", required=True)
    args = parser.parse_args()

    plan = build_phase5_evidence_collection_plan(
        source_tree=args.source_tree,
        phase5_evidence_status_path=args.phase5_evidence_status,
    )
    print(json.dumps(plan, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
