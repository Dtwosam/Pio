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
TEMPLATE_ARTIFACT_TYPE = "PHASE8_PAIRED_ML_PAPER_ENTRY_INPUT_TEMPLATE_V1"
VERIFICATION_ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_ENTRY_INPUT_VERIFICATION_V1"
)

PAPER_EVIDENCE_INPUT_TOOL = Path(
    "deploy/tools/build_phase8_paper_evidence_inputs.py"
)
PAIRED_ENTRY_MODULE = Path(
    "python-learner/src/meteora_learner/paper_ml_pair_entry.py"
)
REVIEWED_SOURCE_BLOBS = {
    PAPER_EVIDENCE_INPUT_TOOL: "9f8671843514eb9d881485dab45fd16841aab921",
    PAIRED_ENTRY_MODULE: "d685e580e7242c1a399a800772d7c8f751aadfc5",
}

PAIR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

FIXED_ENTRY_POLICY = {
    "lookback_observations": 12,
    "half_widths": [0, 1, 2, 5, 10],
    "center_offsets": [0],
    "strategies": ["SPOT", "CURVE", "BID_ASK"],
    "max_share_bps": 500,
    "favor_x_in_active_bin": False,
    "near_liquidity_radius": 5,
    "risk_lambda": 1.5,
    "min_positive_excess_probability": 0.55,
    "min_range_survival_probability": 0.50,
    "min_score_bps": 0.0,
}

TEMPLATE_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "source_post_audit_sha256",
    "source_paper_evidence_input_verification_sha256",
    "source_paper_evidence_input_sha256",
    "production_repository",
    "pio_database_path",
    "model_id",
    "active_cycle_id",
    "account_mode",
    "account_id",
    "starting_cash_quote",
    "pair_id",
    "pool_address",
    "amount_x",
    "amount_y",
    "network_cost_y_atomic",
    "capital_quote",
    "entry_cost_quote",
    "as_of",
    *FIXED_ENTRY_POLICY.keys(),
    "required_manual_fields",
    "incumbent_position_id",
    "challenger_position_id",
    "incumbent_event_key",
    "challenger_event_key",
    "paired_entry_inputs_ready",
    "requires_paper_account_creation_before_pair",
    "requires_fresh_account_readiness_check",
    "requires_separate_paired_entry_authorization",
    "requires_post_pair_audit",
    "paper_pair_entry_authorized",
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
    "source_paper_evidence_input_verification_sha256",
    "source_paper_evidence_input_sha256",
    "entry_input_sha256",
    "production_repository",
    "pio_database_path",
    "model_id",
    "active_cycle_id",
    "account_mode",
    "account_id",
    "starting_cash_quote",
    "pair_id",
    "pool_address",
    "amount_x",
    "amount_y",
    "network_cost_y_atomic",
    "capital_quote",
    "entry_cost_quote",
    "as_of",
    *FIXED_ENTRY_POLICY.keys(),
    "incumbent_position_id",
    "challenger_position_id",
    "incumbent_event_key",
    "challenger_event_key",
    "paper_evidence_input_binding_valid",
    "economic_inputs_valid",
    "entry_policy_not_weakened",
    "paired_entry_inputs_ready",
    "requires_paper_account_creation_before_pair",
    "requires_fresh_account_readiness_check",
    "requires_separate_paired_entry_authorization",
    "requires_post_pair_audit",
    "paper_pair_entry_authorized",
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


def _load_reviewed(source: Path) -> Any:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"reviewed paired PAPER input dependency is missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"reviewed paired PAPER input dependency mismatch: {relative}"
            )
    return _load_module(
        source / PAPER_EVIDENCE_INPUT_TOOL,
        "phase8_paired_paper_entry_evidence_inputs",
    )


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


def _paper_evidence_verification(
    *,
    source: Path,
    module: Any,
    post_audit_path: str | Path,
    paper_evidence_input_path: str | Path,
) -> dict[str, Any]:
    verification = module.verify_phase8_paper_evidence_inputs(
        source_tree=source,
        post_audit_path=post_audit_path,
        input_path=paper_evidence_input_path,
    )
    if verification.get("paper_evidence_inputs_ready") is not True:
        raise ValueError("Phase 8 PAPER evidence inputs are not ready")
    for field in (
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "new_live_capital_authorized",
        "phase8_promotion_authorized",
    ):
        if verification.get(field) is not False:
            raise ValueError(
                f"Phase 8 PAPER evidence verification requires {field}=false"
            )
    return verification


def _base(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_post_audit_sha256": verification[
            "source_post_audit_sha256"
        ],
        "source_paper_evidence_input_verification_sha256": verification[
            "verification_sha256"
        ],
        "source_paper_evidence_input_sha256": verification["input_sha256"],
        "production_repository": verification["production_repository"],
        "pio_database_path": verification["pio_database_path"],
        "model_id": verification["model_id"],
        "active_cycle_id": verification["active_cycle_id"],
        "account_mode": verification["account_mode"],
        "account_id": verification["account_id"],
        "starting_cash_quote": verification["starting_cash_quote"],
    }


def _derived_ids(pair_id: str) -> tuple[str, str, str, str]:
    prefix = f"p8-{pair_id}"
    return (
        f"{prefix}-incumbent",
        f"{prefix}-challenger",
        f"{prefix}:incumbent",
        f"{prefix}:challenger",
    )


def build_phase8_paired_ml_paper_entry_input_template(
    *,
    source_tree: str | Path,
    post_audit_path: str | Path,
    paper_evidence_input_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    module = _load_reviewed(source)
    verification = _paper_evidence_verification(
        source=source,
        module=module,
        post_audit_path=post_audit_path,
        paper_evidence_input_path=paper_evidence_input_path,
    )
    base = _base(verification)
    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": TEMPLATE_ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        **base,
        "pair_id": None,
        "pool_address": None,
        "amount_x": None,
        "amount_y": None,
        "network_cost_y_atomic": None,
        "capital_quote": None,
        "entry_cost_quote": None,
        "as_of": None,
        **FIXED_ENTRY_POLICY,
        "required_manual_fields": [
            "pair_id",
            "pool_address",
            "amount_x",
            "amount_y",
            "network_cost_y_atomic",
            "capital_quote",
            "entry_cost_quote",
        ],
        "incumbent_position_id": None,
        "challenger_position_id": None,
        "incumbent_event_key": None,
        "challenger_event_key": None,
        "paired_entry_inputs_ready": False,
        "requires_paper_account_creation_before_pair": (
            base["account_mode"] == "CREATE"
        ),
        "requires_fresh_account_readiness_check": True,
        "requires_separate_paired_entry_authorization": True,
        "requires_post_pair_audit": True,
        "paper_pair_entry_authorized": False,
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


def _finite_number(
    value: Any,
    *,
    label: str,
    positive: bool,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{label} must be positive")
    if not positive and result < 0:
        raise ValueError(f"{label} cannot be negative")
    return result


def verify_phase8_paired_ml_paper_entry_inputs(
    *,
    source_tree: str | Path,
    post_audit_path: str | Path,
    paper_evidence_input_path: str | Path,
    input_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    module = _load_reviewed(source)
    verification = _paper_evidence_verification(
        source=source,
        module=module,
        post_audit_path=post_audit_path,
        paper_evidence_input_path=paper_evidence_input_path,
    )
    expected = build_phase8_paired_ml_paper_entry_input_template(
        source_tree=source,
        post_audit_path=post_audit_path,
        paper_evidence_input_path=paper_evidence_input_path,
    )
    value = _load_json(input_path, label="Phase 8 paired ML PAPER entry inputs")

    if set(value) != set(TEMPLATE_FIELDS) | {"template_sha256"}:
        raise ValueError("Phase 8 paired PAPER entry input schema mismatch")
    if value.get("format_version") != FORMAT_VERSION:
        raise ValueError("Phase 8 paired PAPER entry input format mismatch")
    if value.get("artifact_type") != TEMPLATE_ARTIFACT_TYPE:
        raise ValueError(
            "Phase 8 paired PAPER entry input artifact type mismatch"
        )
    for field in (
        "reviewed_source_blobs",
        "source_post_audit_sha256",
        "source_paper_evidence_input_verification_sha256",
        "source_paper_evidence_input_sha256",
        "production_repository",
        "pio_database_path",
        "model_id",
        "active_cycle_id",
        "account_mode",
        "account_id",
        "starting_cash_quote",
        "required_manual_fields",
        "requires_paper_account_creation_before_pair",
        "requires_fresh_account_readiness_check",
        "requires_separate_paired_entry_authorization",
        "requires_post_pair_audit",
    ):
        if value.get(field) != expected.get(field):
            raise ValueError(
                f"Phase 8 paired PAPER entry input {field} binding mismatch"
            )
    if value.get("template_sha256") != expected["template_sha256"]:
        raise ValueError(
            "Phase 8 paired PAPER entry template digest mismatch"
        )
    for field, expected_value in FIXED_ENTRY_POLICY.items():
        if value.get(field) != expected_value:
            raise ValueError(
                f"Phase 8 paired PAPER entry policy {field} changed"
            )
    for field in (
        "paired_entry_inputs_ready",
        "paper_pair_entry_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "new_live_capital_authorized",
        "phase8_promotion_authorized",
    ):
        if value.get(field) is not False:
            raise ValueError(
                f"Phase 8 paired PAPER entry input requires {field}=false"
            )

    pair_id = value.get("pair_id")
    if not isinstance(pair_id, str) or PAIR_ID_RE.fullmatch(pair_id) is None:
        raise ValueError("Phase 8 paired PAPER entry pair_id is invalid")
    pool = value.get("pool_address")
    if not isinstance(pool, str) or not pool.strip() or len(pool) > 128:
        raise ValueError("Phase 8 paired PAPER entry pool_address is invalid")

    amount_x = value.get("amount_x")
    amount_y = value.get("amount_y")
    if (
        isinstance(amount_x, bool)
        or not isinstance(amount_x, int)
        or amount_x < 0
        or isinstance(amount_y, bool)
        or not isinstance(amount_y, int)
        or amount_y < 0
        or (amount_x == 0 and amount_y == 0)
    ):
        raise ValueError(
            "Phase 8 paired PAPER entry token amounts are invalid"
        )
    network_cost = value.get("network_cost_y_atomic")
    if (
        isinstance(network_cost, bool)
        or not isinstance(network_cost, int)
        or network_cost < 0
    ):
        raise ValueError(
            "Phase 8 paired PAPER entry network cost is invalid"
        )
    capital = _finite_number(
        value.get("capital_quote"),
        label="Phase 8 paired PAPER entry capital_quote",
        positive=True,
    )
    entry_cost = _finite_number(
        value.get("entry_cost_quote"),
        label="Phase 8 paired PAPER entry entry_cost_quote",
        positive=False,
    )
    if value.get("as_of") is not None:
        raise ValueError(
            "Phase 8 paired PAPER entry input as_of must remain null; "
            "fresh readiness resolves the decision snapshot"
        )

    ids = _derived_ids(pair_id)
    for field, resolved in zip(
        (
            "incumbent_position_id",
            "challenger_position_id",
            "incumbent_event_key",
            "challenger_event_key",
        ),
        ids,
    ):
        if value.get(field) not in (None, resolved):
            raise ValueError(
                f"Phase 8 paired PAPER entry {field} does not match pair_id"
            )

    input_sha = _sha256_bytes(_canonical_bytes(value))
    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": VERIFICATION_ARTIFACT_TYPE,
        "reviewed_source_blobs": expected["reviewed_source_blobs"],
        **_base(verification),
        "entry_input_sha256": input_sha,
        "pair_id": pair_id,
        "pool_address": pool,
        "amount_x": amount_x,
        "amount_y": amount_y,
        "network_cost_y_atomic": network_cost,
        "capital_quote": capital,
        "entry_cost_quote": entry_cost,
        "as_of": None,
        **FIXED_ENTRY_POLICY,
        "incumbent_position_id": ids[0],
        "challenger_position_id": ids[1],
        "incumbent_event_key": ids[2],
        "challenger_event_key": ids[3],
        "paper_evidence_input_binding_valid": True,
        "economic_inputs_valid": True,
        "entry_policy_not_weakened": True,
        "paired_entry_inputs_ready": True,
        "requires_paper_account_creation_before_pair": (
            verification["account_mode"] == "CREATE"
        ),
        "requires_fresh_account_readiness_check": True,
        "requires_separate_paired_entry_authorization": True,
        "requires_post_pair_audit": True,
        "paper_pair_entry_authorized": False,
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
            "Build or verify the explicit economic inputs for one fair paired "
            "ML_CHAMPION vs ML_CHALLENGER PAPER entry. This stage never "
            "creates an account, opens positions, starts a supervisor, uses "
            "live capital, submits transactions, or promotes the challenger."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("template", "verify"):
        item = sub.add_parser(name)
        item.add_argument("--source-tree", required=True)
        item.add_argument("--post-audit", required=True)
        item.add_argument("--paper-evidence-inputs", required=True)
        if name == "verify":
            item.add_argument("--file", required=True)

    args = parser.parse_args()
    if args.command == "template":
        value = build_phase8_paired_ml_paper_entry_input_template(
            source_tree=args.source_tree,
            post_audit_path=args.post_audit,
            paper_evidence_input_path=args.paper_evidence_inputs,
        )
    else:
        value = verify_phase8_paired_ml_paper_entry_inputs(
            source_tree=args.source_tree,
            post_audit_path=args.post_audit,
            paper_evidence_input_path=args.paper_evidence_inputs,
            input_path=args.file,
        )
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
