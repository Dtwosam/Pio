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
TEMPLATE_ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_INPUT_TEMPLATE_V1"
)
VERIFICATION_ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_INPUT_VERIFICATION_V1"
)

FINAL_EVALUATION_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_recursive_rollover_post_settlement_final_evaluation.py"
)
REVIEWED_SOURCE_BLOBS = {
    FINAL_EVALUATION_TOOL: "2d3a6c655627c155082acb2279940076c0d4a0e8",
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
    "source_final_evaluation_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "pio_wal_sha256",
    "pio_shm_sha256",
    "active_cycle_id",
    "incumbent_model_id",
    "challenger_model_id",
    "account_mode",
    "account_id",
    "previous_pair_id",
    "previous_incumbent_closed_trades",
    "previous_challenger_closed_trades",
    "required_incumbent_closed_trades",
    "required_challenger_closed_trades",
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
    "repeat_pair_inputs_ready",
    "requires_fresh_account_readiness_check",
    "requires_separate_paired_entry_authorization",
    "requires_post_pair_audit",
    "paper_pair_entry_authorized",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "continuous_promotion_authorized",
    "phase8_promotion_authorized",
)

VERIFICATION_FIELDS = (
    *TEMPLATE_FIELDS,
    "entry_input_sha256",
    "final_evaluation_binding_valid",
    "economic_inputs_valid",
    "entry_policy_not_weakened",
    "new_pair_id_verified",
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


def _load_final_evaluation(source: Path) -> Any:
    path = source / FINAL_EVALUATION_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed paired PAPER final evaluation is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[FINAL_EVALUATION_TOOL]:
        raise ValueError(
            "reviewed paired PAPER final evaluation blob mismatch"
        )
    return _load_module(
        path,
        "phase8_paired_paper_recursive_rollover_reentry_final_evaluation",
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


def _derived_ids(pair_id: str) -> tuple[str, str, str, str]:
    prefix = f"p8-{pair_id}"
    return (
        f"{prefix}-incumbent",
        f"{prefix}-challenger",
        f"{prefix}:incumbent",
        f"{prefix}:challenger",
    )


def _base(
    source: Path,
    final_evaluation_path: str | Path,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    module = _load_final_evaluation(source)
    evaluation = _load_json(
        final_evaluation_path,
        label="paired PAPER final evaluation",
    )
    module.validate_phase8_paired_ml_paper_recursive_rollover_post_settlement_final_evaluation(
        evaluation
    )
    if evaluation.get("next_pair_review_ready") is not True:
        raise ValueError(
            "paired PAPER final evaluation does not route to another pair"
        )
    if evaluation.get("promotion_review_ready") is not False:
        raise ValueError(
            "paired PAPER recursive rollover re-entry refuses promotion-ready evaluation"
        )
    if evaluation.get("validation_review_ready") is not False:
        raise ValueError(
            "paired PAPER recursive rollover re-entry refuses validation-review evaluation"
        )
    if evaluation.get("closed_trade_floor_met") is not False:
        raise ValueError(
            "paired PAPER recursive rollover re-entry requires closed-trade floor unmet"
        )
    if evaluation.get("next_debt_type") != module.DEBT_MORE_EVIDENCE:
        raise ValueError("paired PAPER recursive rollover re-entry debt binding mismatch")
    if evaluation.get("continuation_route") != module.ROUTE_NEXT_PAIR:
        raise ValueError("paired PAPER recursive rollover re-entry route binding mismatch")
    if evaluation.get("pair_both_closed") is not True:
        raise ValueError("paired PAPER recursive rollover re-entry requires settled prior pair")

    base = {
        "source_final_evaluation_sha256": evaluation["evaluation_sha256"],
        "production_repository": evaluation["production_repository"],
        "pio_database_path": evaluation["pio_database_path"],
        "pio_database_sha256": evaluation["pio_database_sha256_after"],
        "pio_wal_sha256": evaluation["pio_wal_sha256_after"],
        "pio_shm_sha256": evaluation["pio_shm_sha256_after"],
        "active_cycle_id": evaluation["active_cycle_id"],
        "incumbent_model_id": evaluation["incumbent_model_id"],
        "challenger_model_id": evaluation["challenger_model_id"],
        "account_mode": "EXISTING",
        "account_id": evaluation["account_id"],
        "previous_pair_id": evaluation["pair_id"],
        "previous_incumbent_closed_trades": evaluation[
            "incumbent_closed_trades"
        ],
        "previous_challenger_closed_trades": evaluation[
            "challenger_closed_trades"
        ],
        "required_incumbent_closed_trades": evaluation[
            "required_incumbent_closed_trades"
        ],
        "required_challenger_closed_trades": evaluation[
            "required_challenger_closed_trades"
        ],
    }
    return module, evaluation, base


def build_phase8_paired_ml_paper_recursive_rollover_reentry_input_template(
    *,
    source_tree: str | Path,
    final_evaluation_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    _, _, base = _base(source, final_evaluation_path)
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
        "repeat_pair_inputs_ready": False,
        "requires_fresh_account_readiness_check": True,
        "requires_separate_paired_entry_authorization": True,
        "requires_post_pair_audit": True,
        "paper_pair_entry_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
    }
    return {
        **identity,
        "template_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }


def verify_phase8_paired_ml_paper_recursive_rollover_reentry_inputs(
    *,
    source_tree: str | Path,
    final_evaluation_path: str | Path,
    input_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    _, _, base = _base(source, final_evaluation_path)
    expected = build_phase8_paired_ml_paper_recursive_rollover_reentry_input_template(
        source_tree=source,
        final_evaluation_path=final_evaluation_path,
    )
    value = _load_json(
        input_path,
        label="Phase 8 paired ML PAPER repeat rollover entry inputs",
    )

    if set(value) != set(TEMPLATE_FIELDS) | {"template_sha256"}:
        raise ValueError("Phase 8 repeat rollover pair entry input schema mismatch")
    if value.get("format_version") != FORMAT_VERSION:
        raise ValueError("Phase 8 repeat rollover pair entry input format mismatch")
    if value.get("artifact_type") != TEMPLATE_ARTIFACT_TYPE:
        raise ValueError("Phase 8 repeat rollover pair entry artifact type mismatch")
    for field in (
        "reviewed_source_blobs",
        "source_final_evaluation_sha256",
        "production_repository",
        "pio_database_path",
        "pio_database_sha256",
        "pio_wal_sha256",
        "pio_shm_sha256",
        "active_cycle_id",
        "incumbent_model_id",
        "challenger_model_id",
        "account_mode",
        "account_id",
        "previous_pair_id",
        "previous_incumbent_closed_trades",
        "previous_challenger_closed_trades",
        "required_incumbent_closed_trades",
        "required_challenger_closed_trades",
        "required_manual_fields",
        "requires_fresh_account_readiness_check",
        "requires_separate_paired_entry_authorization",
        "requires_post_pair_audit",
    ):
        if value.get(field) != expected.get(field):
            raise ValueError(
                f"Phase 8 repeat rollover pair entry input {field} binding mismatch"
            )
    if value.get("template_sha256") != expected["template_sha256"]:
        raise ValueError("Phase 8 repeat rollover pair template digest mismatch")

    for field, expected_value in FIXED_ENTRY_POLICY.items():
        if value.get(field) != expected_value:
            raise ValueError(
                f"Phase 8 repeat rollover pair entry policy {field} changed"
            )
    for field in (
        "repeat_pair_inputs_ready",
        "paper_pair_entry_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
    ):
        if value.get(field) is not False:
            raise ValueError(
                f"Phase 8 repeat rollover pair input requires {field}=false"
            )

    pair_id = value.get("pair_id")
    if not isinstance(pair_id, str) or PAIR_ID_RE.fullmatch(pair_id) is None:
        raise ValueError("Phase 8 repeat rollover pair pair_id is invalid")
    if pair_id == base["previous_pair_id"]:
        raise ValueError("Phase 8 repeat rollover pair pair_id must be new")

    pool = value.get("pool_address")
    if not isinstance(pool, str) or not pool.strip() or len(pool) > 128:
        raise ValueError("Phase 8 repeat rollover pair pool_address is invalid")

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
        raise ValueError("Phase 8 repeat rollover pair token amounts are invalid")
    network_cost = value.get("network_cost_y_atomic")
    if (
        isinstance(network_cost, bool)
        or not isinstance(network_cost, int)
        or network_cost < 0
    ):
        raise ValueError("Phase 8 repeat rollover pair network cost is invalid")
    capital = _finite_number(
        value.get("capital_quote"),
        label="Phase 8 repeat rollover pair capital_quote",
        positive=True,
    )
    entry_cost = _finite_number(
        value.get("entry_cost_quote"),
        label="Phase 8 repeat rollover pair entry_cost_quote",
        positive=False,
    )
    if value.get("as_of") is not None:
        raise ValueError(
            "Phase 8 repeat rollover pair as_of must remain null; "
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
                f"Phase 8 repeat rollover pair {field} does not match pair_id"
            )

    input_sha = _sha256_bytes(_canonical_bytes(value))
    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": VERIFICATION_ARTIFACT_TYPE,
        "reviewed_source_blobs": expected["reviewed_source_blobs"],
        **base,
        "pair_id": pair_id,
        "pool_address": pool,
        "amount_x": amount_x,
        "amount_y": amount_y,
        "network_cost_y_atomic": network_cost,
        "capital_quote": capital,
        "entry_cost_quote": entry_cost,
        "as_of": None,
        **FIXED_ENTRY_POLICY,
        "required_manual_fields": expected["required_manual_fields"],
        "incumbent_position_id": ids[0],
        "challenger_position_id": ids[1],
        "incumbent_event_key": ids[2],
        "challenger_event_key": ids[3],
        "repeat_pair_inputs_ready": True,
        "requires_fresh_account_readiness_check": True,
        "requires_separate_paired_entry_authorization": True,
        "requires_post_pair_audit": True,
        "paper_pair_entry_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
        "entry_input_sha256": input_sha,
        "final_evaluation_binding_valid": True,
        "economic_inputs_valid": True,
        "entry_policy_not_weakened": True,
        "new_pair_id_verified": True,
    }
    return {
        **identity,
        "verification_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify explicit economics for a recursive fair paired "
            "ML_CHAMPION vs ML_CHALLENGER PAPER entry after a fully settled "
            "pair remains below the 20-vs-20 evidence floor. This stage never "
            "opens positions or authorizes PAPER/live execution."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("template", "verify"):
        item = sub.add_parser(name)
        item.add_argument("--source-tree", required=True)
        item.add_argument("--final-evaluation", required=True)
        if name == "verify":
            item.add_argument("--file", required=True)
    args = parser.parse_args()

    if args.command == "template":
        value = build_phase8_paired_ml_paper_recursive_rollover_reentry_input_template(
            source_tree=args.source_tree,
            final_evaluation_path=args.final_evaluation,
        )
    else:
        value = verify_phase8_paired_ml_paper_recursive_rollover_reentry_inputs(
            source_tree=args.source_tree,
            final_evaluation_path=args.final_evaluation,
            input_path=args.file,
        )
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
