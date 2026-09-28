from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import stat
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_ONE_SHOT_PROOF_REQUEST_V1"

POST_MUTATION_AUDIT_TOOL = Path(
    "deploy/tools/check_manual_market_paper_preserved_post_mutation.py"
)
PROOF_CLI = Path(
    "python-learner/src/meteora_learner/manual_market_paper_cycle_cli.py"
)
PROOF_CYCLE = Path(
    "python-learner/src/meteora_learner/manual_market_paper_cycle.py"
)
RUNTIME_MANIFEST = Path("deploy/manifests/market-paper-manual-cycle.json")

REVIEWED_SOURCE_BLOBS = {
    POST_MUTATION_AUDIT_TOOL: "f23bae2b423fe68778c75cb1131ed5822902d5d6a",
    PROOF_CLI: "87175be0cf5398162c2af42d0a9475d694e02d6f",
    PROOF_CYCLE: "d115f3d2884be0f059cdbacfad24181e994a201d",
    RUNTIME_MANIFEST: "1891afe2c88e267a3dd76fdcdb717a49bc94b15c",
}

PROOF_SCOPE = "ONE_SHOT_MANUAL_PAPER_PROOF_ONLY"
PROOF_MODULE = "meteora_learner.manual_market_paper_cycle_cli"

EXCLUDED_SCOPES = (
    "ACCOUNT_CREATION",
    "SERVICE_RESTART",
    "DETECTOR_CURSOR_MOVEMENT",
    "PAPER_TIMER_ENABLE",
    "PERSISTENT_AUTOMATION",
    "POLICY_ACTION",
    "TRANSACTION_SIGNING",
    "TRANSACTION_SUBMISSION",
    "LIVE_CAPITAL",
)

ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
DECIMAL_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,12})?$")

MAX_NEW_POSITIONS = 1
MAX_POOLS_CONSIDERED_LIMIT = 10
MIN_CHAIN_OBSERVATIONS_FLOOR = 12
INTAKE_MAX_POOLS_LIMIT = 500
SEED_BATCH_LIMIT_MAX = 25
REFRESH_BATCH_LIMIT_MAX = 10
DISCOVERY_PAGE_SIZE_MAX = 1000
DISCOVERY_MAX_PAGES_MAX = 100
DISCOVERY_SORT_BY = "tvl:desc"
BIN_ARRAY_RADIUS = 0
TIMEOUT_SECONDS_MAX = 120
QUOTE_MAX_AGE_SECONDS_MAX = 300
MAX_SHARE_BPS_LIMIT = 500

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
    "scheduler_max_positions",
    "observed_at_mode",
)

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "post_mutation_audit_sha256",
    "production_repository",
    "proof_scope",
    "excluded_scopes",
    "proof_module",
    "parameters",
    "argv",
    "post_mutation_audit_ready",
    "manual_paper_runtime_ready",
    "manual_mode_safe",
    "operational_services_healthy",
    "runtime_files_deployed",
    "paper_only",
    "manual_only",
    "policy_actionable",
    "live_authorized",
    "requires_existing_paper_account",
    "requires_fresh_account_precheck",
    "requires_fresh_proof_precheck",
    "explicit_human_authorization_required",
    "signed_authorization_present",
    "paper_proof_execution_authorized",
    "account_creation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "persistent_automation_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
    "paper_state_modified",
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
    st = resolved.stat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_reviewed_audit_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"reviewed proof-request dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"reviewed proof-request dependency mismatch: {relative}"
            )
    return _load_module(
        source / POST_MUTATION_AUDIT_TOOL,
        "manual_market_paper_one_shot_proof_request_audit",
    )


def _identifier(raw: Any, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(raw, str) or pattern.fullmatch(raw) is None:
        raise ValueError(f"{label} is invalid")
    return raw


def _decimal_string(
    raw: Any,
    *,
    label: str,
    strictly_positive: bool,
) -> str:
    if not isinstance(raw, str) or DECIMAL_RE.fullmatch(raw) is None:
        raise ValueError(f"{label} must be a plain non-negative decimal string")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not value.is_finite():
        raise ValueError(f"{label} must be finite")
    if strictly_positive and value <= 0:
        raise ValueError(f"{label} must be greater than zero")
    if not strictly_positive and value < 0:
        raise ValueError(f"{label} must be non-negative")
    return raw


def _bounded_int(
    value: Any,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise ValueError(f"{label} must be in [{minimum}, {maximum}]")
    return value


def _parameters(
    *,
    account: str,
    run_id: str,
    capital_per_position_quote: str,
    network_cost_quote: str,
    max_pools_considered: int,
    minimum_chain_observations: int,
    intake_max_pools: int,
    seed_batch_limit: int,
    refresh_batch_limit: int,
    discovery_page_size: int,
    discovery_max_pages: int,
    timeout_seconds: int,
    quote_max_age_seconds: int,
    max_share_bps: int,
) -> dict[str, Any]:
    return {
        "account": _identifier(
            account,
            label="proof account",
            pattern=ACCOUNT_RE,
        ),
        "run_id": _identifier(
            run_id,
            label="proof run_id",
            pattern=RUN_ID_RE,
        ),
        "capital_per_position_quote": _decimal_string(
            capital_per_position_quote,
            label="capital_per_position_quote",
            strictly_positive=True,
        ),
        "network_cost_quote": _decimal_string(
            network_cost_quote,
            label="network_cost_quote",
            strictly_positive=False,
        ),
        "max_new_positions": MAX_NEW_POSITIONS,
        "max_pools_considered": _bounded_int(
            max_pools_considered,
            label="max_pools_considered",
            minimum=1,
            maximum=MAX_POOLS_CONSIDERED_LIMIT,
        ),
        "minimum_chain_observations": _bounded_int(
            minimum_chain_observations,
            label="minimum_chain_observations",
            minimum=MIN_CHAIN_OBSERVATIONS_FLOOR,
            maximum=100000,
        ),
        "intake_max_pools": _bounded_int(
            intake_max_pools,
            label="intake_max_pools",
            minimum=1,
            maximum=INTAKE_MAX_POOLS_LIMIT,
        ),
        "seed_batch_limit": _bounded_int(
            seed_batch_limit,
            label="seed_batch_limit",
            minimum=1,
            maximum=SEED_BATCH_LIMIT_MAX,
        ),
        "refresh_batch_limit": _bounded_int(
            refresh_batch_limit,
            label="refresh_batch_limit",
            minimum=1,
            maximum=REFRESH_BATCH_LIMIT_MAX,
        ),
        "discovery_page_size": _bounded_int(
            discovery_page_size,
            label="discovery_page_size",
            minimum=1,
            maximum=DISCOVERY_PAGE_SIZE_MAX,
        ),
        "discovery_max_pages": _bounded_int(
            discovery_max_pages,
            label="discovery_max_pages",
            minimum=1,
            maximum=DISCOVERY_MAX_PAGES_MAX,
        ),
        "discovery_sort_by": DISCOVERY_SORT_BY,
        "bin_array_radius": BIN_ARRAY_RADIUS,
        "timeout_seconds": _bounded_int(
            timeout_seconds,
            label="timeout_seconds",
            minimum=1,
            maximum=TIMEOUT_SECONDS_MAX,
        ),
        "quote_max_age_seconds": _bounded_int(
            quote_max_age_seconds,
            label="quote_max_age_seconds",
            minimum=1,
            maximum=QUOTE_MAX_AGE_SECONDS_MAX,
        ),
        "max_share_bps": _bounded_int(
            max_share_bps,
            label="max_share_bps",
            minimum=1,
            maximum=MAX_SHARE_BPS_LIMIT,
        ),
        "scheduler_max_positions": None,
        "observed_at_mode": "LIVE_NOW",
    }


def _argv(parameters: dict[str, Any]) -> list[str]:
    return [
        "--account",
        parameters["account"],
        "--run-id",
        parameters["run_id"],
        "--capital-per-position",
        parameters["capital_per_position_quote"],
        "--network-cost-quote",
        parameters["network_cost_quote"],
        "--max-new-positions",
        str(parameters["max_new_positions"]),
        "--max-pools-considered",
        str(parameters["max_pools_considered"]),
        "--minimum-chain-observations",
        str(parameters["minimum_chain_observations"]),
        "--intake-max-pools",
        str(parameters["intake_max_pools"]),
        "--seed-batch-limit",
        str(parameters["seed_batch_limit"]),
        "--refresh-batch-limit",
        str(parameters["refresh_batch_limit"]),
        "--discovery-page-size",
        str(parameters["discovery_page_size"]),
        "--discovery-max-pages",
        str(parameters["discovery_max_pages"]),
        "--discovery-sort-by",
        parameters["discovery_sort_by"],
        "--bin-array-radius",
        str(parameters["bin_array_radius"]),
        "--timeout-seconds",
        str(parameters["timeout_seconds"]),
        "--quote-max-age-seconds",
        str(parameters["quote_max_age_seconds"]),
        "--max-share-bps",
        str(parameters["max_share_bps"]),
    ]


def validate_proof_request(request: dict[str, Any]) -> None:
    if not isinstance(request, dict):
        raise ValueError("manual PAPER proof request must be a JSON object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("manual PAPER proof request fields do not match schema")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported manual PAPER proof request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected manual PAPER proof request artifact type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("manual PAPER proof request source lineage mismatch")
    for field in ("post_mutation_audit_sha256", "request_sha256"):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(f"manual PAPER proof request {field} is invalid")

    repository = request.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("manual PAPER proof request repository is invalid")
    if request.get("proof_scope") != PROOF_SCOPE:
        raise ValueError("manual PAPER proof request scope mismatch")
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError("manual PAPER proof request excluded scopes mismatch")
    if request.get("proof_module") != PROOF_MODULE:
        raise ValueError("manual PAPER proof request module mismatch")

    params = request.get("parameters")
    if not isinstance(params, dict) or set(params) != set(PARAMETER_FIELDS):
        raise ValueError("manual PAPER proof request parameter schema mismatch")
    expected_params = _parameters(
        account=params["account"],
        run_id=params["run_id"],
        capital_per_position_quote=params["capital_per_position_quote"],
        network_cost_quote=params["network_cost_quote"],
        max_pools_considered=params["max_pools_considered"],
        minimum_chain_observations=params["minimum_chain_observations"],
        intake_max_pools=params["intake_max_pools"],
        seed_batch_limit=params["seed_batch_limit"],
        refresh_batch_limit=params["refresh_batch_limit"],
        discovery_page_size=params["discovery_page_size"],
        discovery_max_pages=params["discovery_max_pages"],
        timeout_seconds=params["timeout_seconds"],
        quote_max_age_seconds=params["quote_max_age_seconds"],
        max_share_bps=params["max_share_bps"],
    )
    if params != expected_params:
        raise ValueError("manual PAPER proof request parameters are not canonical")
    if request.get("argv") != _argv(params):
        raise ValueError("manual PAPER proof request argv mismatch")

    for field in (
        "post_mutation_audit_ready",
        "manual_paper_runtime_ready",
        "manual_mode_safe",
        "operational_services_healthy",
        "runtime_files_deployed",
        "paper_only",
        "manual_only",
        "requires_existing_paper_account",
        "requires_fresh_account_precheck",
        "requires_fresh_proof_precheck",
        "explicit_human_authorization_required",
    ):
        if request.get(field) is not True:
            raise ValueError(f"manual PAPER proof request requires {field}=true")

    for field in (
        "policy_actionable",
        "live_authorized",
        "signed_authorization_present",
        "paper_proof_execution_authorized",
        "account_creation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "persistent_automation_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "paper_state_modified",
    ):
        if request.get(field) is not False:
            raise ValueError(f"manual PAPER proof request requires {field}=false")

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if request["request_sha256"] != expected_digest:
        raise ValueError("manual PAPER proof request digest mismatch")


def build_proof_request(
    *,
    source_tree: str | Path,
    post_mutation_audit_path: str | Path,
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
    source_candidate = Path(source_tree).expanduser()
    if source_candidate.is_symlink():
        raise ValueError("reviewed source tree must not be a symlink")
    source = source_candidate.resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    audit_module = _load_reviewed_audit_module(source)
    audit = _load_json(
        post_mutation_audit_path,
        label="preserved post-mutation audit",
    )
    audit_module.validate_post_mutation_audit(audit)

    if audit.get("post_mutation_audit_ready") is not True:
        raise ValueError("preserved post-mutation audit is not ready")
    if audit.get("runtime_files_deployed") is not True:
        raise ValueError("manual PAPER runtime files are not deployed")
    if audit.get("manual_mode_safe") is not True:
        raise ValueError("manual PAPER mode is not safe")
    if audit.get("operational_services_healthy") is not True:
        raise ValueError("operational detector/watcher services are not healthy")
    if audit.get("manual_paper_runtime_ready") is not True:
        raise ValueError("manual PAPER runtime is not ready")
    if audit.get("activation_observed") is not False:
        raise ValueError("activation was observed before manual PAPER proof request")

    params = _parameters(
        account=account,
        run_id=run_id,
        capital_per_position_quote=capital_per_position_quote,
        network_cost_quote=network_cost_quote,
        max_pools_considered=max_pools_considered,
        minimum_chain_observations=minimum_chain_observations,
        intake_max_pools=intake_max_pools,
        seed_batch_limit=seed_batch_limit,
        refresh_batch_limit=refresh_batch_limit,
        discovery_page_size=discovery_page_size,
        discovery_max_pages=discovery_max_pages,
        timeout_seconds=timeout_seconds,
        quote_max_age_seconds=quote_max_age_seconds,
        max_share_bps=max_share_bps,
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
        "post_mutation_audit_sha256": audit["post_mutation_audit_sha256"],
        "production_repository": audit["production_repository"],
        "proof_scope": PROOF_SCOPE,
        "excluded_scopes": list(EXCLUDED_SCOPES),
        "proof_module": PROOF_MODULE,
        "parameters": params,
        "argv": _argv(params),
        "post_mutation_audit_ready": True,
        "manual_paper_runtime_ready": True,
        "manual_mode_safe": True,
        "operational_services_healthy": True,
        "runtime_files_deployed": True,
        "paper_only": True,
        "manual_only": True,
        "policy_actionable": False,
        "live_authorized": False,
        "requires_existing_paper_account": True,
        "requires_fresh_account_precheck": True,
        "requires_fresh_proof_precheck": True,
        "explicit_human_authorization_required": True,
        "signed_authorization_present": False,
        "paper_proof_execution_authorized": False,
        "account_creation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "persistent_automation_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "paper_state_modified": False,
    }
    request = {
        **identity,
        "request_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_proof_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing request for one exact manual PAPER-only "
            "proof cycle after a ready preserved post-mutation audit. The request "
            "binds one account, run ID, virtual-capital/cost values and bounded "
            "cycle parameters. It does not inspect account state, execute the "
            "cycle, create an account, enable automation, or grant authorization."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--post-mutation-audit", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--capital-per-position-quote", required=True)
    parser.add_argument("--network-cost-quote", required=True)
    parser.add_argument(
        "--max-pools-considered",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--minimum-chain-observations",
        type=int,
        default=12,
    )
    parser.add_argument("--intake-max-pools", type=int, default=500)
    parser.add_argument("--seed-batch-limit", type=int, default=25)
    parser.add_argument("--refresh-batch-limit", type=int, default=10)
    parser.add_argument("--discovery-page-size", type=int, default=1000)
    parser.add_argument("--discovery-max-pages", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--quote-max-age-seconds", type=int, default=300)
    parser.add_argument("--max-share-bps", type=int, default=500)
    args = parser.parse_args()

    request = build_proof_request(
        source_tree=args.source_tree,
        post_mutation_audit_path=args.post_mutation_audit,
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
