from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_MANUAL_CYCLE_AUTHORIZATION_REQUEST_V1"

READINESS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_manual_cycle_readiness.py"
)
MANUAL_CYCLE_CLI = Path(
    "python-learner/src/meteora_learner/manual_market_paper_cycle_cli.py"
)
MANUAL_CYCLE_CORE = Path(
    "python-learner/src/meteora_learner/manual_market_paper_cycle.py"
)

REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "1e75644f9b4ea33738e8c4e7fddbdc4d08c98826",
    MANUAL_CYCLE_CLI: "87175be0cf5398162c2af42d0a9475d694e02d6f",
    MANUAL_CYCLE_CORE: "d115f3d2884be0f059cdbacfad24181e994a201d",
}

AUTHORIZATION_SCOPE = "ONE_BOUNDED_MANUAL_MARKET_PAPER_CYCLE"
EXCLUDED_SCOPES = (
    "RECURRING_PAPER_TIMER_ENABLE",
    "SERVICE_RESTART",
    "DETECTOR_CURSOR_MOVEMENT",
    "TRANSACTION_SIGNING",
    "TRANSACTION_SUBMISSION",
    "LIVE_CAPITAL",
    "PRODUCTION_FILE_MUTATION",
    "PRODUCTION_GIT_MUTATION",
)

SCHEDULER_INTERVAL_SECONDS = 300
SCHEDULER_LEASE_SECONDS = 900

TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/+~-]{0,127}$")
DECIMAL_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,18})?$")

PARAMETER_FIELDS = (
    "account",
    "run_id",
    "capital_per_position_quote",
    "network_cost_quote",
    "max_new_positions",
    "max_pools_considered",
    "minimum_chain_observations",
    "intake_max_pools",
    "seed_batch_limit",
    "refresh_batch_limit",
    "discovery_page_size",
    "discovery_max_pages",
    "discovery_sort_by",
    "bin_array_radius",
    "timeout_seconds",
    "quote_max_age_seconds",
    "max_share_bps",
    "scheduler_interval_seconds",
    "scheduler_lease_seconds",
    "scheduler_max_positions",
    "observed_at",
)

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "manual_cycle_readiness_sha256",
    "post_mutation_audit_sha256",
    "mutation_receipt_sha256",
    "execution_precheck_sha256",
    "production_repository",
    "approver_principal",
    "approval_id",
    "authorization_scope",
    "excluded_scopes",
    "cycle_parameters",
    "cycle_parameters_sha256",
    "authorization_request_ready",
    "one_cycle_only",
    "paper_only",
    "manual_cycle_authorization_present",
    "manual_cycle_execution_authorized",
    "explicit_human_authorization_required",
    "fresh_manual_cycle_readiness_recheck_required",
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


def _load_readiness_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"manual-cycle authorization-request dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"manual-cycle authorization-request dependency mismatch: {relative}"
            )
    return _load_module(
        source / READINESS_TOOL,
        "manual_market_paper_cycle_authorization_request_readiness",
    )


def _token(raw: Any, *, label: str) -> str:
    if not isinstance(raw, str) or TOKEN_RE.fullmatch(raw) is None:
        raise ValueError(f"manual-cycle {label} is invalid")
    return raw


def _decimal_text(
    raw: Any,
    *,
    label: str,
    allow_zero: bool,
) -> str:
    if not isinstance(raw, str) or DECIMAL_RE.fullmatch(raw) is None:
        raise ValueError(f"manual-cycle {label} must be canonical decimal text")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"manual-cycle {label} is invalid") from exc
    if not value.is_finite():
        raise ValueError(f"manual-cycle {label} must be finite")
    if allow_zero:
        if value < 0:
            raise ValueError(f"manual-cycle {label} must be non-negative")
    elif value <= 0:
        raise ValueError(f"manual-cycle {label} must be positive")
    return raw


def _bounded_int(
    raw: Any,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if (
        not isinstance(raw, int)
        or isinstance(raw, bool)
        or raw < minimum
        or raw > maximum
    ):
        raise ValueError(
            f"manual-cycle {label} must be in {minimum}..{maximum}"
        )
    return raw


def _validate_parameters(parameters: dict[str, Any]) -> None:
    if not isinstance(parameters, dict) or set(parameters) != set(PARAMETER_FIELDS):
        raise ValueError("manual-cycle authorization parameters schema mismatch")

    _token(parameters.get("account"), label="account")
    _token(parameters.get("run_id"), label="run_id")
    _decimal_text(
        parameters.get("capital_per_position_quote"),
        label="capital_per_position_quote",
        allow_zero=False,
    )
    _decimal_text(
        parameters.get("network_cost_quote"),
        label="network_cost_quote",
        allow_zero=True,
    )

    if parameters.get("max_new_positions") != 1:
        raise ValueError("manual-cycle first proof must cap max_new_positions at 1")
    _bounded_int(
        parameters.get("max_pools_considered"),
        label="max_pools_considered",
        minimum=1,
        maximum=10,
    )
    _bounded_int(
        parameters.get("minimum_chain_observations"),
        label="minimum_chain_observations",
        minimum=12,
        maximum=1000,
    )
    _bounded_int(
        parameters.get("intake_max_pools"),
        label="intake_max_pools",
        minimum=1,
        maximum=500,
    )
    _bounded_int(
        parameters.get("seed_batch_limit"),
        label="seed_batch_limit",
        minimum=1,
        maximum=25,
    )
    _bounded_int(
        parameters.get("refresh_batch_limit"),
        label="refresh_batch_limit",
        minimum=1,
        maximum=10,
    )
    _bounded_int(
        parameters.get("discovery_page_size"),
        label="discovery_page_size",
        minimum=1,
        maximum=1000,
    )
    _bounded_int(
        parameters.get("discovery_max_pages"),
        label="discovery_max_pages",
        minimum=1,
        maximum=100,
    )
    if parameters.get("discovery_sort_by") != "tvl:desc":
        raise ValueError("manual-cycle discovery_sort_by must be tvl:desc")
    if parameters.get("bin_array_radius") != 0:
        raise ValueError("manual-cycle first proof requires bin_array_radius=0")
    _bounded_int(
        parameters.get("timeout_seconds"),
        label="timeout_seconds",
        minimum=1,
        maximum=120,
    )
    _bounded_int(
        parameters.get("quote_max_age_seconds"),
        label="quote_max_age_seconds",
        minimum=1,
        maximum=300,
    )
    _bounded_int(
        parameters.get("max_share_bps"),
        label="max_share_bps",
        minimum=1,
        maximum=500,
    )
    if parameters.get("scheduler_interval_seconds") != SCHEDULER_INTERVAL_SECONDS:
        raise ValueError("manual-cycle scheduler interval drifted")
    if parameters.get("scheduler_lease_seconds") != SCHEDULER_LEASE_SECONDS:
        raise ValueError("manual-cycle scheduler lease drifted")
    if parameters.get("scheduler_max_positions") != 1:
        raise ValueError(
            "manual-cycle first proof requires scheduler_max_positions=1"
        )
    if parameters.get("observed_at") is not None:
        raise ValueError(
            "manual-cycle first proof must use fresh runtime time, not observed_at"
        )


def validate_manual_cycle_authorization_request(
    request: dict[str, Any],
) -> None:
    if not isinstance(request, dict):
        raise ValueError("manual-cycle authorization request must be a JSON object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("manual-cycle authorization request schema mismatch")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported manual-cycle authorization request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected manual-cycle authorization request type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("manual-cycle authorization request lineage mismatch")

    for field in (
        "manual_cycle_readiness_sha256",
        "post_mutation_audit_sha256",
        "mutation_receipt_sha256",
        "execution_precheck_sha256",
        "cycle_parameters_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(
                f"manual-cycle authorization request {field} is invalid"
            )

    repository = request.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError(
            "manual-cycle authorization request production repository is invalid"
        )
    for field in ("approver_principal", "approval_id"):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError(
                f"manual-cycle authorization request {field} is invalid"
            )

    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("manual-cycle authorization request scope mismatch")
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError(
            "manual-cycle authorization request excluded scopes mismatch"
        )

    parameters = request.get("cycle_parameters")
    _validate_parameters(parameters)
    expected_parameters_sha = hashlib.sha256(
        _canonical_bytes(parameters)
    ).hexdigest()
    if request.get("cycle_parameters_sha256") != expected_parameters_sha:
        raise ValueError(
            "manual-cycle authorization request parameter digest mismatch"
        )

    for field in (
        "authorization_request_ready",
        "one_cycle_only",
        "paper_only",
        "explicit_human_authorization_required",
        "fresh_manual_cycle_readiness_recheck_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                f"manual-cycle authorization request requires {field}=true"
            )

    for field in (
        "manual_cycle_authorization_present",
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
        if request.get(field) is not False:
            raise ValueError(
                f"manual-cycle authorization request requires {field}=false"
            )

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if request["request_sha256"] != expected_digest:
        raise ValueError("manual-cycle authorization request digest mismatch")


def build_manual_cycle_authorization_request(
    *,
    source_tree: str | Path,
    readiness_path: str | Path,
    account: str,
    run_id: str,
    capital_per_position_quote: str,
    network_cost_quote: str,
    max_pools_considered: int = 10,
    minimum_chain_observations: int = 12,
    intake_max_pools: int = 500,
    seed_batch_limit: int = 25,
    refresh_batch_limit: int = 10,
    discovery_page_size: int = 1000,
    discovery_max_pages: int = 100,
    timeout_seconds: int = 120,
    quote_max_age_seconds: int = 300,
    max_share_bps: int = 500,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    readiness_module = _load_readiness_module(source)

    readiness = _load_json(
        readiness_path,
        label="manual-cycle readiness artifact",
    )
    readiness_module.validate_manual_cycle_readiness(readiness)
    if readiness.get("manual_cycle_readiness_ready") is not True:
        raise ValueError("manual-cycle readiness is not ready")
    if readiness.get("manual_cycle_execution_authorized") is not False:
        raise ValueError(
            "manual-cycle readiness unexpectedly authorizes execution"
        )
    if readiness.get("paper_timer_enable_authorized") is not False:
        raise ValueError("manual-cycle readiness unexpectedly authorizes timer")

    parameters = {
        "account": _token(account, label="account"),
        "run_id": _token(run_id, label="run_id"),
        "capital_per_position_quote": _decimal_text(
            capital_per_position_quote,
            label="capital_per_position_quote",
            allow_zero=False,
        ),
        "network_cost_quote": _decimal_text(
            network_cost_quote,
            label="network_cost_quote",
            allow_zero=True,
        ),
        "max_new_positions": 1,
        "max_pools_considered": max_pools_considered,
        "minimum_chain_observations": minimum_chain_observations,
        "intake_max_pools": intake_max_pools,
        "seed_batch_limit": seed_batch_limit,
        "refresh_batch_limit": refresh_batch_limit,
        "discovery_page_size": discovery_page_size,
        "discovery_max_pages": discovery_max_pages,
        "discovery_sort_by": "tvl:desc",
        "bin_array_radius": 0,
        "timeout_seconds": timeout_seconds,
        "quote_max_age_seconds": quote_max_age_seconds,
        "max_share_bps": max_share_bps,
        "scheduler_interval_seconds": SCHEDULER_INTERVAL_SECONDS,
        "scheduler_lease_seconds": SCHEDULER_LEASE_SECONDS,
        "scheduler_max_positions": 1,
        "observed_at": None,
    }
    _validate_parameters(parameters)

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
        "manual_cycle_readiness_sha256": readiness[
            "manual_cycle_readiness_sha256"
        ],
        "post_mutation_audit_sha256": readiness[
            "fresh_post_mutation_audit_sha256"
        ],
        "mutation_receipt_sha256": readiness["mutation_receipt_sha256"],
        "execution_precheck_sha256": readiness["execution_precheck_sha256"],
        "production_repository": readiness["production_repository"],
        "approver_principal": readiness["approver_principal"],
        "approval_id": readiness["approval_id"],
        "authorization_scope": AUTHORIZATION_SCOPE,
        "excluded_scopes": list(EXCLUDED_SCOPES),
        "cycle_parameters": parameters,
        "cycle_parameters_sha256": hashlib.sha256(
            _canonical_bytes(parameters)
        ).hexdigest(),
        "authorization_request_ready": True,
        "one_cycle_only": True,
        "paper_only": True,
        "manual_cycle_authorization_present": False,
        "manual_cycle_execution_authorized": False,
        "explicit_human_authorization_required": True,
        "fresh_manual_cycle_readiness_recheck_required": True,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
    }
    request = {
        **identity,
        "request_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_manual_cycle_authorization_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing request for exactly one bounded manual "
            "market/PAPER cycle. The request binds the fresh readiness lineage "
            "and all cycle parameters, but contains no human approval and does "
            "not execute the cycle, enable automation, restart services, move "
            "detector state, sign/submit transactions, or use live capital."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--readiness", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--capital-per-position-quote", required=True)
    parser.add_argument("--network-cost-quote", required=True)
    parser.add_argument("--max-pools-considered", type=int, default=10)
    parser.add_argument("--minimum-chain-observations", type=int, default=12)
    parser.add_argument("--intake-max-pools", type=int, default=500)
    parser.add_argument("--seed-batch-limit", type=int, default=25)
    parser.add_argument("--refresh-batch-limit", type=int, default=10)
    parser.add_argument("--discovery-page-size", type=int, default=1000)
    parser.add_argument("--discovery-max-pages", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--quote-max-age-seconds", type=int, default=300)
    parser.add_argument("--max-share-bps", type=int, default=500)
    args = parser.parse_args()

    request = build_manual_cycle_authorization_request(
        source_tree=args.source_tree,
        readiness_path=args.readiness,
        account=args.account,
        run_id=args.run_id,
        capital_per_position_quote=args.capital_per_position_quote,
        network_cost_quote=args.network_cost_quote,
        max_pools_considered=args.max_pools_considered,
        minimum_chain_observations=args.minimum_chain_observations,
        intake_max_pools=args.intake_max_pools,
        seed_batch_limit=args.seed_batch_limit,
        refresh_batch_limit=args.refresh_batch_limit,
        discovery_page_size=args.discovery_page_size,
        discovery_max_pages=args.discovery_max_pages,
        timeout_seconds=args.timeout_seconds,
        quote_max_age_seconds=args.quote_max_age_seconds,
        max_share_bps=args.max_share_bps,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
