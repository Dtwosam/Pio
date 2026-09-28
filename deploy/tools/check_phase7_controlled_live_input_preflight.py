from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
from typing import Any
import uuid


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_INPUT_PREFLIGHT_V1"

EVIDENCE_PLAN_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_evidence_plan.py"
)
CONTROLLED_LIVE_MODULE = Path("rust-executor/src/controlled_live.rs")
MODELS_MODULE = Path("rust-executor/src/models.rs")

REVIEWED_SOURCE_BLOBS = {
    EVIDENCE_PLAN_TOOL: "3afb813bc08a57bc0a9d1a8d2df4439e4bfa8824",
    CONTROLLED_LIVE_MODULE: "5686a9578a3f6191d9fc1106cf75569c966dea69",
    MODELS_MODULE: "dd2b7c8857ecc6a28b224f600927120260e36935",
}

BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

CONFIG_FIELDS = (
    "enabled",
    "allowed_pool_addresses",
    "max_open_positions",
    "max_rebalances_per_position",
    "max_capital_quote_per_entry",
    "max_daily_entry_capital_quote",
    "max_daily_entry_submissions",
    "max_daily_realized_loss_quote",
    "max_daily_drawdown_pct",
    "allow_rebalance",
    "allow_exit",
)

PROPOSAL_FIELDS = (
    "decision_id",
    "mode",
    "action",
    "pool_address",
    "capital_quote",
    "account_equity_quote",
    "portfolio_deployed_quote",
    "daily_drawdown_pct",
    "min_bin_id",
    "max_bin_id",
    "strategy",
    "expected_net_return_pct",
    "expected_downside_pct",
    "model_version",
    "data_age_seconds",
)

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "phase7_evidence_plan_sha256",
    "phase7_evidence_status_sha256",
    "phase6_post_promotion_audit_sha256",
    "controlled_live_config",
    "controlled_live_config_sha256",
    "proposal",
    "proposal_sha256",
    "executor_wallet_pubkey",
    "input_manifest_sha256",
    "single_pool_scope",
    "single_position_scope",
    "single_daily_entry_scope",
    "rebalance_disabled",
    "exit_enabled",
    "proposal_capital_equals_entry_cap",
    "daily_entry_cap_equals_proposal_capital",
    "loss_budget_within_proposal_capital",
    "proposal_drawdown_within_config",
    "input_preflight_ready",
    "explicit_human_authorization_required",
    "fresh_phase6_readiness_required",
    "rust_controlled_live_check_required",
    "rust_risk_gate_required",
    "exact_presign_evidence_required",
    "proposal_transaction_account_binding_required",
    "fresh_blockhash_required",
    "final_simulation_required",
    "wallet_authorization_required",
    "live_submit_feature_required",
    "runtime_live_submit_opt_in_required",
    "confirmation_receipt_reconciliation_required",
    "phase7_promotion_separate",
    "controlled_live_authorization_present",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "phase7_promotion_persisted",
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


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


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


def _load_plan_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 7 input-preflight dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 input-preflight dependency mismatch: {relative}"
            )
    return _load_module(
        source / EVIDENCE_PLAN_TOOL,
        "phase7_controlled_live_input_preflight_plan",
    )


def _finite_number(
    value: Any,
    *,
    label: str,
    minimum: float | None = None,
    positive: bool = False,
) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"Phase 7 {label} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"Phase 7 {label} must be finite")
    if positive and normalized <= 0:
        raise ValueError(f"Phase 7 {label} must be positive")
    if minimum is not None and normalized < minimum:
        raise ValueError(f"Phase 7 {label} is below the minimum")
    return normalized


def _pubkey(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or BASE58_RE.fullmatch(value) is None:
        raise ValueError(f"Phase 7 {label} is not a canonical public key")
    return value


def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict) or set(config) != set(CONFIG_FIELDS):
        raise ValueError("Phase 7 controlled-LIVE config schema mismatch")

    if config.get("enabled") is not True:
        raise ValueError("Phase 7 first-live config must be explicitly enabled")
    pools = config.get("allowed_pool_addresses")
    if not isinstance(pools, list) or len(pools) != 1:
        raise ValueError("Phase 7 first-live config requires exactly one pool")
    _pubkey(pools[0], label="allowed pool")
    if pools[0] == "REPLACE_WITH_APPROVED_POOL":
        raise ValueError("Phase 7 first-live config cannot use placeholder pool")

    if config.get("max_open_positions") != 1:
        raise ValueError("Phase 7 first-live config requires max_open_positions=1")
    if config.get("max_rebalances_per_position") != 1:
        raise ValueError(
            "Phase 7 first-live config requires max_rebalances_per_position=1"
        )
    if config.get("max_daily_entry_submissions") != 1:
        raise ValueError(
            "Phase 7 first-live config requires max_daily_entry_submissions=1"
        )
    if config.get("allow_rebalance") is not False:
        raise ValueError("Phase 7 first-live config must disable REBALANCE")
    if config.get("allow_exit") is not True:
        raise ValueError("Phase 7 first-live config must preserve EXIT")

    for field in (
        "max_capital_quote_per_entry",
        "max_daily_entry_capital_quote",
        "max_daily_realized_loss_quote",
    ):
        _finite_number(config.get(field), label=field, positive=True)
    drawdown = _finite_number(
        config.get("max_daily_drawdown_pct"),
        label="max_daily_drawdown_pct",
        minimum=0.0,
    )
    if drawdown > 100.0:
        raise ValueError("Phase 7 max_daily_drawdown_pct cannot exceed 100")
    return config


def _validate_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(proposal, dict) or set(proposal) != set(PROPOSAL_FIELDS):
        raise ValueError("Phase 7 first-live proposal schema mismatch")

    raw_decision = proposal.get("decision_id")
    if not isinstance(raw_decision, str):
        raise ValueError("Phase 7 proposal decision_id is invalid")
    try:
        decision = uuid.UUID(raw_decision)
    except ValueError as exc:
        raise ValueError("Phase 7 proposal decision_id is invalid") from exc
    if str(decision) != raw_decision:
        raise ValueError("Phase 7 proposal decision_id must be canonical")

    if proposal.get("mode") != "LIVE":
        raise ValueError("Phase 7 first-live proposal mode must be LIVE")
    if proposal.get("action") != "ENTER":
        raise ValueError("Phase 7 first-live proposal action must be ENTER")
    _pubkey(proposal.get("pool_address"), label="proposal pool")

    _finite_number(
        proposal.get("capital_quote"),
        label="proposal capital_quote",
        positive=True,
    )
    _finite_number(
        proposal.get("account_equity_quote"),
        label="proposal account_equity_quote",
        positive=True,
    )
    _finite_number(
        proposal.get("portfolio_deployed_quote"),
        label="proposal portfolio_deployed_quote",
        minimum=0.0,
    )
    _finite_number(
        proposal.get("daily_drawdown_pct"),
        label="proposal daily_drawdown_pct",
        minimum=0.0,
    )
    _finite_number(
        proposal.get("expected_net_return_pct"),
        label="proposal expected_net_return_pct",
    )
    _finite_number(
        proposal.get("expected_downside_pct"),
        label="proposal expected_downside_pct",
        minimum=0.0,
    )

    for field in ("min_bin_id", "max_bin_id", "data_age_seconds"):
        value = proposal.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"Phase 7 proposal {field} must be an integer")
    if proposal["min_bin_id"] > proposal["max_bin_id"]:
        raise ValueError("Phase 7 proposal bin range is invalid")
    if proposal["data_age_seconds"] < 0:
        raise ValueError("Phase 7 proposal data_age_seconds cannot be negative")

    for field in ("strategy", "model_version"):
        value = proposal.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Phase 7 proposal {field} is invalid")
    return proposal


def validate_phase7_input_preflight(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 input preflight must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"input_preflight_sha256"}:
        raise ValueError("Phase 7 input preflight schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 input preflight format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 input preflight type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 input preflight lineage mismatch")

    for field in (
        "phase7_evidence_plan_sha256",
        "phase7_evidence_status_sha256",
        "phase6_post_promotion_audit_sha256",
        "controlled_live_config_sha256",
        "proposal_sha256",
        "input_manifest_sha256",
        "input_preflight_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 input preflight {field} is invalid")

    config = report.get("controlled_live_config")
    proposal = report.get("proposal")
    _validate_config(config)
    _validate_proposal(proposal)
    wallet = _pubkey(
        report.get("executor_wallet_pubkey"),
        label="executor wallet",
    )
    if wallet == proposal["pool_address"]:
        raise ValueError("Phase 7 executor wallet cannot equal pool address")

    if report["controlled_live_config_sha256"] != _sha256(config):
        raise ValueError("Phase 7 controlled-LIVE config digest mismatch")
    if report["proposal_sha256"] != _sha256(proposal):
        raise ValueError("Phase 7 proposal digest mismatch")
    expected_manifest = _sha256(
        {
            "controlled_live_config": config,
            "proposal": proposal,
            "executor_wallet_pubkey": wallet,
        }
    )
    if report["input_manifest_sha256"] != expected_manifest:
        raise ValueError("Phase 7 input manifest digest mismatch")

    if config["allowed_pool_addresses"] != [proposal["pool_address"]]:
        raise ValueError("Phase 7 pool allowlist/proposal binding mismatch")
    capital = float(proposal["capital_quote"])
    if float(config["max_capital_quote_per_entry"]) != capital:
        raise ValueError("Phase 7 entry cap must equal proposal capital")
    if float(config["max_daily_entry_capital_quote"]) != capital:
        raise ValueError("Phase 7 daily entry cap must equal proposal capital")
    if float(config["max_daily_realized_loss_quote"]) > capital:
        raise ValueError("Phase 7 loss budget cannot exceed proposal capital")
    if float(proposal["daily_drawdown_pct"]) > float(
        config["max_daily_drawdown_pct"]
    ):
        raise ValueError("Phase 7 proposal drawdown exceeds configured limit")

    for field in (
        "single_pool_scope",
        "single_position_scope",
        "single_daily_entry_scope",
        "rebalance_disabled",
        "exit_enabled",
        "proposal_capital_equals_entry_cap",
        "daily_entry_cap_equals_proposal_capital",
        "loss_budget_within_proposal_capital",
        "proposal_drawdown_within_config",
        "input_preflight_ready",
        "explicit_human_authorization_required",
        "fresh_phase6_readiness_required",
        "rust_controlled_live_check_required",
        "rust_risk_gate_required",
        "exact_presign_evidence_required",
        "proposal_transaction_account_binding_required",
        "fresh_blockhash_required",
        "final_simulation_required",
        "wallet_authorization_required",
        "live_submit_feature_required",
        "runtime_live_submit_opt_in_required",
        "confirmation_receipt_reconciliation_required",
        "phase7_promotion_separate",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 7 input preflight requires {field}=true")

    for field in (
        "controlled_live_authorization_present",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 7 input preflight requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["input_preflight_sha256"] != _sha256(identity):
        raise ValueError("Phase 7 input preflight digest mismatch")


def build_phase7_input_preflight(
    *,
    source_tree: str | Path,
    phase7_evidence_plan_path: str | Path,
    controlled_live_config_path: str | Path,
    proposal_path: str | Path,
    executor_wallet_pubkey: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    plan_module = _load_plan_module(source)

    plan = _load_json(
        phase7_evidence_plan_path,
        label="Phase 7 controlled-LIVE evidence plan",
    )
    plan_module.validate_phase7_evidence_plan(plan)
    if plan.get("collection_plan_ready") is not True:
        raise ValueError("Phase 7 evidence plan is not ready")
    if plan.get("new_entry_evidence_candidate") is not True:
        raise ValueError("Phase 7 evidence plan does not permit new entry evidence")
    if plan.get("controlled_live_inputs_required") is not True:
        raise ValueError("Phase 7 evidence plan does not require explicit live inputs")
    if plan.get("phase7_promotion_ready") is True:
        raise ValueError("Phase 7 evidence is already promotion-ready")

    for field in (
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
    ):
        if plan.get(field) is not False:
            raise ValueError(f"Phase 7 evidence plan unexpectedly authorizes {field}")

    config = _validate_config(
        _load_json(
            controlled_live_config_path,
            label="Phase 7 controlled-LIVE config",
        )
    )
    proposal = _validate_proposal(
        _load_json(
            proposal_path,
            label="Phase 7 first-live proposal",
        )
    )
    wallet = _pubkey(executor_wallet_pubkey, label="executor wallet")

    if config["allowed_pool_addresses"] != [proposal["pool_address"]]:
        raise ValueError("Phase 7 pool allowlist must equal the proposal pool")
    if wallet == proposal["pool_address"]:
        raise ValueError("Phase 7 executor wallet cannot equal pool address")

    capital = float(proposal["capital_quote"])
    if float(config["max_capital_quote_per_entry"]) != capital:
        raise ValueError("Phase 7 first-live entry cap must equal proposal capital")
    if float(config["max_daily_entry_capital_quote"]) != capital:
        raise ValueError("Phase 7 first-live daily cap must equal proposal capital")
    if float(config["max_daily_realized_loss_quote"]) > capital:
        raise ValueError("Phase 7 first-live loss budget cannot exceed proposal capital")
    if float(proposal["daily_drawdown_pct"]) > float(
        config["max_daily_drawdown_pct"]
    ):
        raise ValueError("Phase 7 proposal drawdown exceeds configured limit")

    manifest = {
        "controlled_live_config": config,
        "proposal": proposal,
        "executor_wallet_pubkey": wallet,
    }

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
        "phase7_evidence_plan_sha256": plan["plan_sha256"],
        "phase7_evidence_status_sha256": plan["phase7_evidence_status_sha256"],
        "phase6_post_promotion_audit_sha256": plan[
            "phase6_post_promotion_audit_sha256"
        ],
        "controlled_live_config": config,
        "controlled_live_config_sha256": _sha256(config),
        "proposal": proposal,
        "proposal_sha256": _sha256(proposal),
        "executor_wallet_pubkey": wallet,
        "input_manifest_sha256": _sha256(manifest),
        "single_pool_scope": True,
        "single_position_scope": True,
        "single_daily_entry_scope": True,
        "rebalance_disabled": True,
        "exit_enabled": True,
        "proposal_capital_equals_entry_cap": True,
        "daily_entry_cap_equals_proposal_capital": True,
        "loss_budget_within_proposal_capital": True,
        "proposal_drawdown_within_config": True,
        "input_preflight_ready": True,
        "explicit_human_authorization_required": True,
        "fresh_phase6_readiness_required": True,
        "rust_controlled_live_check_required": True,
        "rust_risk_gate_required": True,
        "exact_presign_evidence_required": True,
        "proposal_transaction_account_binding_required": True,
        "fresh_blockhash_required": True,
        "final_simulation_required": True,
        "wallet_authorization_required": True,
        "live_submit_feature_required": True,
        "runtime_live_submit_opt_in_required": True,
        "confirmation_receipt_reconciliation_required": True,
        "phase7_promotion_separate": True,
        "controlled_live_authorization_present": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
    }
    report = {
        **identity,
        "input_preflight_sha256": _sha256(identity),
    }
    validate_phase7_input_preflight(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and seal explicit operator-owned inputs for one narrow "
            "Phase 7 controlled-LIVE ENTER evidence attempt. This preflight "
            "never chooses a pool, wallet, capital or budget, never accesses "
            "production, and never authorizes signing, submission or live capital."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase7-evidence-plan", required=True)
    parser.add_argument("--controlled-live-config", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--executor-wallet-pubkey", required=True)
    args = parser.parse_args()

    report = build_phase7_input_preflight(
        source_tree=args.source_tree,
        phase7_evidence_plan_path=args.phase7_evidence_plan,
        controlled_live_config_path=args.controlled_live_config,
        proposal_path=args.proposal,
        executor_wallet_pubkey=args.executor_wallet_pubkey,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
