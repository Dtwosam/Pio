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


FORMAT_VERSION = 1
TEMPLATE_ARTIFACT_TYPE = "PHASE8_PAPER_EVIDENCE_INPUT_TEMPLATE_V1"
VERIFICATION_ARTIFACT_TYPE = "PHASE8_PAPER_EVIDENCE_INPUT_VERIFICATION_V1"

POST_AUDIT_TOOL = Path(
    "deploy/tools/check_phase8_paper_challenger_transition_post_audit.py"
)
REVIEWED_SOURCE_BLOBS = {
    POST_AUDIT_TOOL: "763d093146efab317c28804ffc6fbb10d4130c7d",
}

ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+/-]{0,127}$")
ACCOUNT_MODES = {"EXISTING", "CREATE"}

FIXED_CRITERIA = {
    "min_challenger_closed_trades": 20,
    "min_incumbent_closed_trades": 20,
    "min_challenger_return_bps": 0,
    "min_challenger_win_rate": 0.50,
    "max_challenger_drawdown_bps": 1500,
    "max_single_trade_loss_bps": 1000,
    "min_uplift_vs_incumbent_bps": 0,
}

TEMPLATE_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "source_post_audit_sha256",
    "production_repository",
    "pio_database_path",
    "model_id",
    "active_cycle_id",
    "debt_type",
    "continuation_route",
    "account_mode",
    "account_id",
    "starting_cash_quote",
    *FIXED_CRITERIA.keys(),
    "required_manual_fields",
    "paper_account_create_command",
    "paper_supervisor_command",
    "continuous_validation_command",
    "paper_evidence_inputs_ready",
    "requires_separate_paper_account_action",
    "requires_separate_paper_evidence_execution_authorization",
    "requires_closed_trade_evidence_before_validation",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "new_live_capital_authorized",
    "phase8_promotion_authorized",
)

VERIFICATION_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "source_post_audit_sha256",
    "input_sha256",
    "production_repository",
    "pio_database_path",
    "model_id",
    "active_cycle_id",
    "debt_type",
    "continuation_route",
    "account_mode",
    "account_id",
    "starting_cash_quote",
    *FIXED_CRITERIA.keys(),
    "paper_account_create_command",
    "paper_supervisor_command",
    "continuous_validation_command",
    "post_audit_binding_valid",
    "account_input_valid",
    "economic_input_valid",
    "criteria_not_weakened",
    "paper_evidence_inputs_ready",
    "requires_separate_paper_account_action",
    "requires_separate_paper_evidence_execution_authorization",
    "requires_closed_trade_evidence_before_validation",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "new_live_capital_authorized",
    "phase8_promotion_authorized",
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


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_post_audit_module(source: Path) -> Any:
    path = source / POST_AUDIT_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed Phase 8 PAPER transition audit is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[POST_AUDIT_TOOL]:
        raise ValueError("reviewed Phase 8 PAPER transition audit blob mismatch")
    return _load_module(path, "phase8_paper_evidence_inputs_post_audit")


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


def _base_from_audit(audit: dict[str, Any]) -> dict[str, Any]:
    if audit.get("continuation_route") != (
        "PHASE8_PAPER_CHALLENGER_EVIDENCE_REVIEW"
    ):
        raise ValueError("Phase 8 audit is not routed to PAPER evidence review")
    if audit.get("next_debt_type") != "PAPER_CHALLENGER_EVIDENCE_REQUIRED":
        raise ValueError("Phase 8 PAPER evidence debt type mismatch")
    if audit.get("paper_challenger_active") is not True:
        raise ValueError("Phase 8 PAPER challenger is not active")
    for field in (
        "paper_account_required",
        "challenger_closed_trade_evidence_required",
        "incumbent_closed_trade_evidence_required",
        "requires_manual_paper_evidence_inputs",
        "requires_separate_paper_evidence_action",
    ):
        if audit.get(field) is not True:
            raise ValueError(f"Phase 8 PAPER evidence audit requires {field}=true")
    for field in (
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "new_live_capital_authorized",
        "phase8_promotion_authorized",
    ):
        if audit.get(field) is not False:
            raise ValueError(f"Phase 8 PAPER evidence audit requires {field}=false")
    return {
        "source_post_audit_sha256": audit["post_audit_sha256"],
        "production_repository": audit["production_repository"],
        "pio_database_path": audit["pio_database_path"],
        "model_id": audit["model_id"],
        "active_cycle_id": audit["active_cycle_id"],
        "debt_type": audit["next_debt_type"],
        "continuation_route": audit["continuation_route"],
    }


def _commands(
    *,
    mode: str | None,
    account_id: str | None,
    starting_cash_quote: float | None,
    cycle_id: str,
) -> tuple[str | None, str | None, str | None]:
    if mode is None or account_id is None:
        return None, None, None
    create = None
    if mode == "CREATE":
        assert starting_cash_quote is not None
        create = (
            "pio paper-create-account --account "
            + account_id
            + " --cash "
            + format(starting_cash_quote, ".15g")
        )
    supervise = (
        "pio paper-supervise --account "
        + account_id
        + " --cycle-id "
        + cycle_id
    )
    validate = (
        "pio ml-continuous-validate --cycle-id "
        + cycle_id
        + " --account "
        + account_id
        + " --min-challenger-closed-trades 20"
        + " --min-incumbent-closed-trades 20"
        + " --min-challenger-return-bps 0"
        + " --min-challenger-win-rate 0.5"
        + " --max-challenger-drawdown-bps 1500"
        + " --max-single-trade-loss-bps 1000"
        + " --min-uplift-vs-incumbent-bps 0"
        + " --require-qualified"
    )
    return create, supervise, validate


def build_phase8_paper_evidence_input_template(
    *,
    source_tree: str | Path,
    post_audit_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    module = _load_post_audit_module(source)
    audit = _load_json(post_audit_path, label="Phase 8 PAPER transition post-audit")
    module.validate_phase8_paper_challenger_transition_post_audit(audit)
    base = _base_from_audit(audit)
    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": TEMPLATE_ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
            )
        },
        **base,
        "account_mode": None,
        "account_id": None,
        "starting_cash_quote": None,
        **FIXED_CRITERIA,
        "required_manual_fields": [
            "account_mode",
            "account_id",
            "starting_cash_quote_if_account_mode_CREATE",
        ],
        "paper_account_create_command": None,
        "paper_supervisor_command": None,
        "continuous_validation_command": None,
        "paper_evidence_inputs_ready": False,
        "requires_separate_paper_account_action": True,
        "requires_separate_paper_evidence_execution_authorization": True,
        "requires_closed_trade_evidence_before_validation": True,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_promotion_authorized": False,
    }
    return {
        **identity,
        "template_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }


def verify_phase8_paper_evidence_inputs(
    *,
    source_tree: str | Path,
    post_audit_path: str | Path,
    input_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    module = _load_post_audit_module(source)
    audit = _load_json(post_audit_path, label="Phase 8 PAPER transition post-audit")
    module.validate_phase8_paper_challenger_transition_post_audit(audit)
    base = _base_from_audit(audit)
    value = _load_json(input_path, label="Phase 8 PAPER evidence inputs")
    expected_template = build_phase8_paper_evidence_input_template(
        source_tree=source,
        post_audit_path=post_audit_path,
    )

    allowed = set(TEMPLATE_FIELDS) | {"template_sha256"}
    if set(value) != allowed:
        raise ValueError("Phase 8 PAPER evidence input schema mismatch")
    for field, expected in base.items():
        if value.get(field) != expected:
            raise ValueError(f"Phase 8 PAPER evidence input {field} binding mismatch")
    if value.get("artifact_type") != TEMPLATE_ARTIFACT_TYPE:
        raise ValueError("Phase 8 PAPER evidence input artifact type mismatch")
    if value.get("format_version") != FORMAT_VERSION:
        raise ValueError("Phase 8 PAPER evidence input format mismatch")
    if value.get("reviewed_source_blobs") != expected_template[
        "reviewed_source_blobs"
    ]:
        raise ValueError("Phase 8 PAPER evidence input lineage mismatch")
    if value.get("template_sha256") != expected_template["template_sha256"]:
        raise ValueError("Phase 8 PAPER evidence template digest mismatch")
    if value.get("required_manual_fields") != expected_template[
        "required_manual_fields"
    ]:
        raise ValueError("Phase 8 PAPER evidence required-field contract changed")
    for field in (
        "paper_evidence_inputs_ready",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "new_live_capital_authorized",
        "phase8_promotion_authorized",
    ):
        if value.get(field) is not False:
            raise ValueError(
                f"Phase 8 PAPER evidence input requires {field}=false"
            )
    for field in (
        "requires_separate_paper_account_action",
        "requires_separate_paper_evidence_execution_authorization",
        "requires_closed_trade_evidence_before_validation",
    ):
        if value.get(field) is not True:
            raise ValueError(
                f"Phase 8 PAPER evidence input requires {field}=true"
            )

    mode = value.get("account_mode")
    account = value.get("account_id")
    cash = value.get("starting_cash_quote")
    if mode not in ACCOUNT_MODES:
        raise ValueError("Phase 8 PAPER evidence account_mode must be EXISTING or CREATE")
    if not isinstance(account, str) or ACCOUNT_RE.fullmatch(account) is None:
        raise ValueError("Phase 8 PAPER evidence account_id is invalid")
    if mode == "CREATE":
        if (
            isinstance(cash, bool)
            or not isinstance(cash, (int, float))
            or not math.isfinite(float(cash))
            or float(cash) <= 0
        ):
            raise ValueError(
                "Phase 8 PAPER evidence CREATE mode requires positive starting_cash_quote"
            )
        normalized_cash: float | None = float(cash)
    else:
        if cash is not None:
            raise ValueError(
                "Phase 8 PAPER evidence EXISTING mode requires starting_cash_quote=null"
            )
        normalized_cash = None

    for field, expected in FIXED_CRITERIA.items():
        if value.get(field) != expected:
            raise ValueError(
                f"Phase 8 PAPER evidence qualification criterion {field} changed"
            )

    create_cmd, supervise_cmd, validate_cmd = _commands(
        mode=mode,
        account_id=account,
        starting_cash_quote=normalized_cash,
        cycle_id=base["active_cycle_id"],
    )
    if value.get("paper_account_create_command") not in (None, create_cmd):
        raise ValueError("Phase 8 PAPER evidence account-create command mismatch")
    if value.get("paper_supervisor_command") not in (None, supervise_cmd):
        raise ValueError("Phase 8 PAPER evidence supervisor command mismatch")
    if value.get("continuous_validation_command") not in (None, validate_cmd):
        raise ValueError("Phase 8 PAPER evidence validation command mismatch")

    input_sha = _sha256_bytes(_canonical_bytes(value))
    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": VERIFICATION_ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
            )
        },
        **base,
        "input_sha256": input_sha,
        "account_mode": mode,
        "account_id": account,
        "starting_cash_quote": normalized_cash,
        **FIXED_CRITERIA,
        "paper_account_create_command": create_cmd,
        "paper_supervisor_command": supervise_cmd,
        "continuous_validation_command": validate_cmd,
        "post_audit_binding_valid": True,
        "account_input_valid": True,
        "economic_input_valid": True,
        "criteria_not_weakened": True,
        "paper_evidence_inputs_ready": True,
        "requires_separate_paper_account_action": mode == "CREATE",
        "requires_separate_paper_evidence_execution_authorization": True,
        "requires_closed_trade_evidence_before_validation": True,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_promotion_authorized": False,
    }
    return {
        **identity,
        "verification_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify the explicit manual inputs required before "
            "Phase 8 PAPER challenger evidence collection. This tool never "
            "creates a paper account, starts paper trades, uses live capital, "
            "submits transactions, or promotes the challenger."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    template = sub.add_parser("template")
    template.add_argument("--source-tree", required=True)
    template.add_argument("--post-audit", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--source-tree", required=True)
    verify.add_argument("--post-audit", required=True)
    verify.add_argument("--file", required=True)

    args = parser.parse_args()
    if args.command == "template":
        value = build_phase8_paper_evidence_input_template(
            source_tree=args.source_tree,
            post_audit_path=args.post_audit,
        )
    else:
        value = verify_phase8_paper_evidence_inputs(
            source_tree=args.source_tree,
            post_audit_path=args.post_audit,
            input_path=args.file,
        )
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
