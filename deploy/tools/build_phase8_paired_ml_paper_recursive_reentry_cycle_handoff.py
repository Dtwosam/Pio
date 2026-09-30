from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FINAL_EVALUATION_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_post_settlement_final_evaluation.py"
)
HANDOFF_CONTRACT_TOOL = Path(
    "deploy/tools/phase8_paired_ml_paper_recursive_reentry_cycle_handoff_contract.py"
)
REVIEWED_SOURCE_BLOBS = {
    FINAL_EVALUATION_TOOL: "afc669b027ea27d160b8a0f1d4d80845add6cbf0",
    HANDOFF_CONTRACT_TOOL: "033e5198cbec6e05621efdf7c4ada34aaadd67ce",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


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


def _load_reviewed(source: Path) -> tuple[Any, Any]:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"recursive re-entry cycle handoff dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"recursive re-entry cycle handoff dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / FINAL_EVALUATION_TOOL,
            "phase8_recursive_reentry_cycle_handoff_final_evaluation",
        ),
        _load_module(
            source / HANDOFF_CONTRACT_TOOL,
            "phase8_recursive_reentry_cycle_handoff_contract",
        ),
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


def _database_state(database: Path) -> dict[str, str | None]:
    if database.is_symlink() or not database.is_file():
        raise ValueError("recursive re-entry cycle handoff database is invalid")
    wal = Path(str(database) + "-wal")
    shm = Path(str(database) + "-shm")
    return {
        "database": _sha256_path(database),
        "wal": _sha256_path(wal) if wal.is_file() else None,
        "shm": _sha256_path(shm) if shm.is_file() else None,
    }


def build_phase8_paired_ml_paper_recursive_reentry_cycle_handoff(
    *,
    source_tree: str | Path,
    final_evaluation_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    evaluation_module, contract_module = _load_reviewed(source)
    evaluation = _load_json(
        final_evaluation_path,
        label="recursive re-entry cycle final evaluation",
    )
    evaluation_module.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_post_settlement_final_evaluation(
        evaluation
    )

    if evaluation.get("pair_both_closed") is not True:
        raise ValueError("recursive re-entry cycle handoff requires fully closed pair")
    if evaluation.get("closed_trade_floor_met") is not False:
        raise ValueError("recursive re-entry cycle handoff requires evidence floor unmet")
    if evaluation.get("next_pair_review_ready") is not True:
        raise ValueError("recursive re-entry cycle handoff is not next-pair ready")
    if evaluation.get("promotion_review_ready") is not False:
        raise ValueError("recursive re-entry cycle handoff refuses promotion-ready state")
    if evaluation.get("validation_review_ready") is not False:
        raise ValueError("recursive re-entry cycle handoff refuses validation-ready state")
    if evaluation.get("next_debt_type") != evaluation_module.DEBT_MORE_EVIDENCE:
        raise ValueError("recursive re-entry cycle handoff debt mismatch")
    if evaluation.get("continuation_route") != evaluation_module.ROUTE_NEXT_PAIR:
        raise ValueError("recursive re-entry cycle handoff route mismatch")
    if evaluation.get("read_only") is not True:
        raise ValueError("recursive re-entry cycle final evaluation is not read-only")

    for field in (
        "new_pair_entry_authorized",
        "continuous_promotion_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "phase8_promotion_authorized",
    ):
        if evaluation.get(field) is not False:
            raise ValueError(
                f"recursive re-entry cycle final evaluation requires {field}=false"
            )

    production = Path(evaluation["production_repository"]).resolve()
    database = Path(evaluation["pio_database_path"]).resolve()
    if not production.is_dir():
        raise ValueError("recursive re-entry cycle handoff repository is invalid")
    try:
        database.relative_to(production)
    except ValueError as exc:
        raise ValueError(
            "recursive re-entry cycle handoff database escapes repository"
        ) from exc

    current = _database_state(database)
    expected = {
        "database": evaluation["pio_database_sha256_after"],
        "wal": evaluation["pio_wal_sha256_after"],
        "shm": evaluation["pio_shm_sha256_after"],
    }
    if current != expected:
        raise ValueError(
            "recursive re-entry cycle final evaluation database state drifted"
        )

    identity = {
        "format_version": contract_module.FORMAT_VERSION,
        "artifact_type": contract_module.ARTIFACT_TYPE,
        "source_final_evaluation_sha256": evaluation["evaluation_sha256"],
        "source_prior_final_evaluation_sha256": evaluation[
            "source_prior_final_evaluation_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": current["database"],
        "pio_wal_sha256": current["wal"],
        "pio_shm_sha256": current["shm"],
        "active_cycle_id": evaluation["active_cycle_id"],
        "incumbent_model_id": evaluation["incumbent_model_id"],
        "challenger_model_id": evaluation["challenger_model_id"],
        "account_id": evaluation["account_id"],
        "previous_pair_id": evaluation["pair_id"],
        "previous_pool_address": evaluation["pool_address"],
        "previous_entry_observed_at": evaluation["entry_observed_at"],
        "previous_final_settlement_observed_at": evaluation[
            "final_settlement_observed_at"
        ],
        "previous_incumbent_position_id": evaluation[
            "incumbent_position_id"
        ],
        "previous_challenger_position_id": evaluation[
            "challenger_position_id"
        ],
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
        "pair_both_closed": True,
        "closed_trade_floor_met": False,
        "next_debt_type": contract_module.DEBT_MORE_EVIDENCE,
        "continuation_route": contract_module.ROUTE_NEXT_PAIR,
        "next_pair_review_ready": True,
        "promotion_review_ready": False,
        "validation_review_ready": False,
        "separate_next_action_authorization_required": True,
        "read_only": True,
        "new_pair_entry_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
    }
    handoff = {
        **identity,
        "handoff_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    contract_module.validate_phase8_paired_ml_paper_recursive_reentry_cycle_handoff(
        handoff
    )
    return handoff


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only stable handoff from a fully settled recursive "
            "re-entry cycle that still needs more paired PAPER evidence."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--final-evaluation", required=True)
    args = parser.parse_args()

    value = build_phase8_paired_ml_paper_recursive_reentry_cycle_handoff(
        source_tree=args.source_tree,
        final_evaluation_path=args.final_evaluation,
    )
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
