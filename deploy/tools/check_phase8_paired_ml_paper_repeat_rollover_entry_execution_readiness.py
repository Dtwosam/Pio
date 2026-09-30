from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_REPEAT_ROLLOVER_ENTRY_EXECUTION_READINESS_V1"
)

ROLLOVER_INPUT_TOOL = Path(
    "deploy/tools/build_phase8_paired_ml_paper_repeat_rollover_entry_inputs.py"
)
ROLLOVER_ACCOUNT_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_repeat_rollover_account_readiness.py"
)
ROLLOVER_REQUEST_TOOL = Path(
    "deploy/tools/build_phase8_paired_ml_paper_repeat_rollover_entry_request.py"
)
ROLLOVER_SIGNER_TOOL = Path(
    "deploy/tools/build_phase8_paired_ml_paper_repeat_rollover_entry_signed_authorization.py"
)
BASE_EXECUTION_READINESS_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_entry_execution_readiness.py"
)
REVIEWED_SOURCE_BLOBS = {
    ROLLOVER_INPUT_TOOL: "9a5b9cbb13cf8035887996f944738e8a4bfd5b0f",
    ROLLOVER_ACCOUNT_TOOL: "fd1e89ca4718ea9b1b9b4589aa5eaa631ec61fc8",
    ROLLOVER_REQUEST_TOOL: "ac9ee6ad488e6deba52515d312ffdd732a2ac192",
    ROLLOVER_SIGNER_TOOL: "b190b924ec7dd9118d1c9662dbcbbe95054f9eff",
    BASE_EXECUTION_READINESS_TOOL: "acfeb1eb595695751773b191e3cf9844074bb2b9",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_account_readiness_sha256",
    "fresh_account_readiness_sha256",
    "rollover_entry_request_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
    "rollover_entry_input_verification_sha256",
    "source_final_evaluation_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "pio_wal_sha256",
    "pio_shm_sha256",
    "active_cycle_id",
    "incumbent_model_id",
    "challenger_model_id",
    "account_id",
    "previous_pair_id",
    "pair_id",
    "pool_address",
    "amount_x",
    "amount_y",
    "network_cost_y_atomic",
    "capital_quote",
    "entry_cost_quote",
    "required_pair_cash_quote",
    "as_of",
    "lookback_observations",
    "half_widths",
    "center_offsets",
    "strategies",
    "max_share_bps",
    "favor_x_in_active_bin",
    "near_liquidity_radius",
    "risk_lambda",
    "min_positive_excess_probability",
    "min_range_survival_probability",
    "min_score_bps",
    "incumbent_position_id",
    "challenger_position_id",
    "incumbent_event_key",
    "challenger_event_key",
    "approver_principal",
    "approval_id",
    "authorization_expires_at",
    "decision_observed_at",
    "candidate_frame_sha256",
    "incumbent_inference_sha256",
    "challenger_inference_sha256",
    "incumbent_choice_sha256",
    "challenger_choice_sha256",
    "incumbent_preflight_sha256",
    "challenger_preflight_sha256",
    "incumbent_choice",
    "challenger_choice",
    "fresh_account_readiness_matches_saved",
    "fresh_request_matches_saved",
    "fresh_authorization_matches_saved",
    "human_repeat_rollover_paired_paper_entry_authorization_verified",
    "account_ready",
    "zero_open_positions_verified",
    "previous_pair_closed_verified",
    "cycle_model_binding_valid",
    "model_inference_readiness_verified",
    "same_candidate_frame_verified",
    "same_decision_snapshot_verified",
    "equal_capital_verified",
    "counterfactual_preflight_verified",
    "no_lookahead_verified",
    "paper_pair_entry_execution_readiness_ready",
    "readiness_only",
    "requires_immediate_one_shot_pair_executor",
    "post_pair_audit_required",
    "paper_pair_entry_authorized",
    "paper_pair_entry_executed",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "continuous_promotion_authorized",
    "phase8_promotion_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified",
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


def _load_reviewed(source: Path) -> tuple[Any, Any, Any, Any, Any]:
    loaded: dict[Path, Any] = {}
    names = {
        ROLLOVER_INPUT_TOOL: "phase8_repeat_rollover_entry_readiness_input",
        ROLLOVER_ACCOUNT_TOOL: "phase8_repeat_rollover_entry_readiness_account",
        ROLLOVER_REQUEST_TOOL: "phase8_repeat_rollover_entry_readiness_request",
        ROLLOVER_SIGNER_TOOL: "phase8_repeat_rollover_entry_readiness_signer",
        BASE_EXECUTION_READINESS_TOOL: "phase8_repeat_rollover_entry_readiness_base",
    }
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"repeat rollover paired PAPER execution readiness dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"repeat rollover paired PAPER execution readiness dependency mismatch: {relative}"
            )
        loaded[relative] = _load_module(path, names[relative])
    return (
        loaded[ROLLOVER_INPUT_TOOL],
        loaded[ROLLOVER_ACCOUNT_TOOL],
        loaded[ROLLOVER_REQUEST_TOOL],
        loaded[ROLLOVER_SIGNER_TOOL],
        loaded[BASE_EXECUTION_READINESS_TOOL],
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


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    elif is_dataclass(value):
        result = asdict(value)
    elif isinstance(value, dict):
        result = value
    else:
        raise ValueError("repeat rollover paired PAPER inference value is not recordable")
    if not isinstance(result, dict):
        raise ValueError("repeat rollover paired PAPER inference record is invalid")
    return result


def _hash_record(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(_record(value)))


def validate_phase8_paired_ml_paper_repeat_rollover_entry_execution_readiness(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "repeat rollover paired PAPER execution readiness must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError(
            "repeat rollover paired PAPER execution readiness schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported repeat rollover paired PAPER execution readiness format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected repeat rollover paired PAPER execution readiness type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "repeat rollover paired PAPER execution readiness lineage mismatch"
        )

    for field in (
        "saved_account_readiness_sha256",
        "fresh_account_readiness_sha256",
        "rollover_entry_request_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "rollover_entry_input_verification_sha256",
        "source_final_evaluation_sha256",
        "pio_database_sha256",
        "candidate_frame_sha256",
        "incumbent_inference_sha256",
        "challenger_inference_sha256",
        "incumbent_choice_sha256",
        "challenger_choice_sha256",
        "incumbent_preflight_sha256",
        "challenger_preflight_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"repeat rollover paired PAPER execution readiness {field} is invalid"
            )
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"repeat rollover paired PAPER execution readiness {field} is invalid"
            )

    for field in (
        "production_repository",
        "pio_database_path",
        "active_cycle_id",
        "incumbent_model_id",
        "challenger_model_id",
        "account_id",
        "previous_pair_id",
        "pair_id",
        "pool_address",
        "incumbent_position_id",
        "challenger_position_id",
        "incumbent_event_key",
        "challenger_event_key",
        "approver_principal",
        "approval_id",
        "authorization_expires_at",
        "decision_observed_at",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"repeat rollover paired PAPER execution readiness {field} is invalid"
            )

    if report["pair_id"] == report["previous_pair_id"]:
        raise ValueError(
            "repeat rollover paired PAPER execution readiness pair id was not advanced"
        )
    if report["incumbent_model_id"] == report["challenger_model_id"]:
        raise ValueError(
            "repeat rollover paired PAPER execution readiness model identities must differ"
        )
    if report["incumbent_position_id"] == report["challenger_position_id"]:
        raise ValueError(
            "repeat rollover paired PAPER execution readiness position ids must differ"
        )

    for field in ("incumbent_choice", "challenger_choice"):
        if not isinstance(report.get(field), dict):
            raise ValueError(
                f"repeat rollover paired PAPER execution readiness {field} is invalid"
            )
        choice = report[field]
        if choice.get("pool_address") != report["pool_address"]:
            raise ValueError(
                "repeat rollover paired PAPER execution readiness choice pool mismatch"
            )
        if choice.get("decision_observed_at") != report[
            "decision_observed_at"
        ]:
            raise ValueError(
                "repeat rollover paired PAPER execution readiness choice decision mismatch"
            )

    for field in (
        "fresh_account_readiness_matches_saved",
        "fresh_request_matches_saved",
        "fresh_authorization_matches_saved",
        "human_repeat_rollover_paired_paper_entry_authorization_verified",
        "account_ready",
        "zero_open_positions_verified",
        "previous_pair_closed_verified",
        "cycle_model_binding_valid",
        "model_inference_readiness_verified",
        "same_candidate_frame_verified",
        "same_decision_snapshot_verified",
        "equal_capital_verified",
        "counterfactual_preflight_verified",
        "no_lookahead_verified",
        "paper_pair_entry_execution_readiness_ready",
        "readiness_only",
        "requires_immediate_one_shot_pair_executor",
        "post_pair_audit_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"repeat rollover paired PAPER execution readiness requires {field}=true"
            )

    for field in (
        "paper_pair_entry_authorized",
        "paper_pair_entry_executed",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"repeat rollover paired PAPER execution readiness requires {field}=false"
            )

    if report["saved_account_readiness_sha256"] != report[
        "fresh_account_readiness_sha256"
    ]:
        raise ValueError(
            "repeat rollover paired PAPER account readiness digest mismatch"
        )
    if report[
        "saved_signed_authorization_verification_sha256"
    ] != report["fresh_signed_authorization_verification_sha256"]:
        raise ValueError(
            "repeat rollover paired PAPER authorization verification digest mismatch"
        )
    if report["required_pair_cash_quote"] != 2.0 * (
        float(report["capital_quote"])
        + float(report["entry_cost_quote"])
    ):
        raise ValueError(
            "repeat rollover paired PAPER execution readiness cash binding mismatch"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["readiness_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "repeat rollover paired PAPER execution readiness digest mismatch"
        )


def build_phase8_paired_ml_paper_repeat_rollover_entry_execution_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    final_evaluation_path: str | Path,
    rollover_entry_input_path: str | Path,
    saved_account_readiness_path: str | Path,
    request_path: str | Path,
    saved_signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    (
        input_module,
        account_module,
        request_module,
        signer_module,
        base_readiness_module,
    ) = _load_reviewed(source)
    base_account_module, _, _, pair_module = (
        base_readiness_module._load_reviewed(source)
    )

    saved_account = _load_json(
        saved_account_readiness_path,
        label="saved repeat rollover paired PAPER account readiness",
    )
    request = _load_json(
        request_path,
        label="repeat rollover paired PAPER entry request",
    )
    saved_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="saved repeat rollover paired PAPER signed authorization verification",
    )
    account_module.validate_phase8_paired_ml_paper_repeat_rollover_account_readiness(
        saved_account
    )
    request_module.validate_phase8_paired_ml_paper_repeat_rollover_entry_request(
        request
    )
    signer_module.validate_verification(saved_verification)

    if request["source_rollover_account_readiness_sha256"] != saved_account[
        "readiness_sha256"
    ]:
        raise ValueError(
            "repeat rollover paired PAPER request/account readiness binding mismatch"
        )
    if saved_verification["request_sha256"] != request["request_sha256"]:
        raise ValueError(
            "repeat rollover paired PAPER signed authorization/request mismatch"
        )

    fresh_account = (
        account_module.build_phase8_paired_ml_paper_repeat_rollover_account_readiness(
            repository=production,
            source_tree=source,
            final_evaluation_path=final_evaluation_path,
            rollover_entry_input_path=rollover_entry_input_path,
        )
    )
    account_module.validate_phase8_paired_ml_paper_repeat_rollover_account_readiness(
        fresh_account
    )
    if fresh_account != saved_account:
        raise ValueError(
            "fresh repeat rollover paired PAPER account readiness differs from saved"
        )
    if fresh_account.get("readiness_status") != account_module.STATUS_READY:
        raise ValueError(
            "fresh repeat rollover paired PAPER account readiness is not READY"
        )
    if fresh_account.get("zero_open_positions_verified") is not True:
        raise ValueError(
            "repeat rollover paired PAPER account no longer has zero open positions"
        )
    if fresh_account.get("previous_pair_positions_closed") is not True:
        raise ValueError(
            "repeat rollover paired PAPER previous pair no longer remains closed"
        )

    with tempfile.TemporaryDirectory(
        prefix="pio-phase8-repeat-pair-readiness-"
    ) as tmp:
        fresh_account_path = Path(tmp) / "account-readiness.json"
        fresh_account_path.write_text(
            json.dumps(fresh_account, sort_keys=True),
            encoding="utf-8",
        )
        fresh_request = (
            request_module.build_phase8_paired_ml_paper_repeat_rollover_entry_request(
                source_tree=source,
                account_readiness_path=fresh_account_path,
            )
        )
    request_module.validate_phase8_paired_ml_paper_repeat_rollover_entry_request(
        fresh_request
    )
    if fresh_request != request:
        raise ValueError(
            "fresh repeat rollover paired PAPER entry request differs from saved"
        )

    fresh_verification = signer_module.verify_authorization(
        source_tree=source,
        request_path=request_path,
        payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=expected_allowed_signers_sha256,
        now=now,
    )
    signer_module.validate_verification(fresh_verification)
    if fresh_verification != saved_verification:
        raise ValueError(
            "fresh repeat rollover paired PAPER authorization differs from saved"
        )

    input_verification = (
        input_module.verify_phase8_paired_ml_paper_repeat_rollover_entry_inputs(
            source_tree=source,
            final_evaluation_path=final_evaluation_path,
            input_path=rollover_entry_input_path,
        )
    )
    if input_verification["verification_sha256"] != request[
        "rollover_entry_input_verification_sha256"
    ]:
        raise ValueError(
            "repeat rollover paired PAPER verified entry inputs changed"
        )

    database = base_account_module._production_database(production)
    if Path(str(request["pio_database_path"])).resolve() != database:
        raise ValueError(
            "repeat rollover paired PAPER request database binding mismatch"
        )
    current_state = base_account_module._database_state(database)
    expected_state = {
        "database": request["pio_database_sha256"],
        "wal": request["pio_wal_sha256"],
        "shm": request["pio_shm_sha256"],
    }
    if current_state != expected_state:
        raise ValueError(
            "Pio database changed after repeat rollover paired PAPER request"
        )

    storage = pair_module.Storage(database)
    cycle = pair_module.retraining_cycle(
        storage,
        cycle_id=request["active_cycle_id"],
    )
    if (
        cycle.status != "PAPER_CHALLENGER"
        or cycle.champion_model_id != request["incumbent_model_id"]
        or cycle.challenger_model_id != request["challenger_model_id"]
    ):
        raise ValueError(
            "repeat rollover paired PAPER cycle/model binding changed"
        )
    pair_module._model_status_record(
        storage,
        model_id=request["incumbent_model_id"],
        required_status="CHAMPION",
    )
    challenger_raw = pair_module._model_status_record(
        storage,
        model_id=request["challenger_model_id"],
        required_status="PAPER_CHALLENGER",
    )
    if str(challenger_raw.get("dataset_version")) != cycle.target_dataset_version:
        raise ValueError(
            "repeat rollover paired PAPER challenger dataset binding changed"
        )

    inference_config = pair_module.MLInferenceConfig(
        risk_lambda=float(input_verification["risk_lambda"]),
        min_positive_excess_probability=float(
            input_verification["min_positive_excess_probability"]
        ),
        min_range_survival_probability=float(
            input_verification["min_range_survival_probability"]
        ),
        min_score_bps=float(input_verification["min_score_bps"]),
    )
    strategies = tuple(
        pair_module.StrategyType(value)
        for value in input_verification["strategies"]
    )
    candidate_report = pair_module.build_current_ml_candidate_frame(
        str(database),
        pool_address=request["pool_address"],
        amount_x=int(input_verification["amount_x"]),
        amount_y=int(input_verification["amount_y"]),
        network_cost_y_atomic=int(
            input_verification["network_cost_y_atomic"]
        ),
        lookback_observations=int(
            input_verification["lookback_observations"]
        ),
        half_widths=tuple(
            int(x) for x in input_verification["half_widths"]
        ),
        center_offsets=tuple(
            int(x) for x in input_verification["center_offsets"]
        ),
        strategies=strategies,
        max_share_bps=int(input_verification["max_share_bps"]),
        favor_x_in_active_bin=bool(
            input_verification["favor_x_in_active_bin"]
        ),
        near_liquidity_radius=int(
            input_verification["near_liquidity_radius"]
        ),
        as_of=input_verification["as_of"],
    )
    if candidate_report.no_lookahead is not True:
        raise ValueError(
            "repeat rollover paired PAPER candidate frame is not no-lookahead"
        )
    frame = candidate_report.to_frame()

    incumbent_bundle = pair_module.load_registered_ml_v1(
        storage,
        model_id=request["incumbent_model_id"],
    )
    challenger_bundle = pair_module.load_registered_ml_v1(
        storage,
        model_id=request["challenger_model_id"],
    )
    incumbent_inference = pair_module.score_ml_candidates(
        incumbent_bundle,
        frame,
        config=inference_config,
    )
    challenger_inference = pair_module.score_ml_candidates(
        challenger_bundle,
        frame,
        config=inference_config,
    )
    if (
        incumbent_inference.policy_actionable is not False
        or challenger_inference.policy_actionable is not False
    ):
        raise ValueError(
            "repeat rollover paired PAPER inference became policy-actionable"
        )
    if incumbent_inference.research_choice is None:
        raise ValueError(
            "repeat rollover paired PAPER incumbent has no eligible choice"
        )
    if challenger_inference.research_choice is None:
        raise ValueError(
            "repeat rollover paired PAPER challenger has no eligible choice"
        )

    incumbent_choice = pair_module._choice(
        model_id=request["incumbent_model_id"],
        policy_source="ML_CHAMPION",
        prediction=incumbent_inference.research_choice,
        frame=frame,
    )
    challenger_choice = pair_module._choice(
        model_id=request["challenger_model_id"],
        policy_source="ML_CHALLENGER",
        prediction=challenger_inference.research_choice,
        frame=frame,
    )
    if (
        incumbent_choice.decision_observed_at
        != candidate_report.decision_observed_at
        or challenger_choice.decision_observed_at
        != candidate_report.decision_observed_at
    ):
        raise ValueError(
            "repeat rollover paired PAPER inference decision snapshot changed"
        )

    incumbent_preflight = pair_module._preflight_choice(
        storage,
        choice=incumbent_choice,
        amount_x=int(input_verification["amount_x"]),
        amount_y=int(input_verification["amount_y"]),
        max_share_bps=int(input_verification["max_share_bps"]),
        favor_x_in_active_bin=bool(
            input_verification["favor_x_in_active_bin"]
        ),
    )
    challenger_preflight = pair_module._preflight_choice(
        storage,
        choice=challenger_choice,
        amount_x=int(input_verification["amount_x"]),
        amount_y=int(input_verification["amount_y"]),
        max_share_bps=int(input_verification["max_share_bps"]),
        favor_x_in_active_bin=bool(
            input_verification["favor_x_in_active_bin"]
        ),
    )

    incumbent_choice_record = _record(incumbent_choice)
    challenger_choice_record = _record(challenger_choice)
    candidate_record = _record(candidate_report)
    incumbent_inference_record = _record(incumbent_inference)
    challenger_inference_record = _record(challenger_inference)

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
            )
        },
        "saved_account_readiness_sha256": saved_account[
            "readiness_sha256"
        ],
        "fresh_account_readiness_sha256": fresh_account[
            "readiness_sha256"
        ],
        "rollover_entry_request_sha256": request["request_sha256"],
        "saved_signed_authorization_verification_sha256": (
            saved_verification["verification_sha256"]
        ),
        "fresh_signed_authorization_verification_sha256": (
            fresh_verification["verification_sha256"]
        ),
        "rollover_entry_input_verification_sha256": input_verification[
            "verification_sha256"
        ],
        "source_final_evaluation_sha256": request[
            "source_final_evaluation_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": current_state["database"],
        "pio_wal_sha256": current_state["wal"],
        "pio_shm_sha256": current_state["shm"],
        "active_cycle_id": request["active_cycle_id"],
        "incumbent_model_id": request["incumbent_model_id"],
        "challenger_model_id": request["challenger_model_id"],
        "account_id": request["account_id"],
        "previous_pair_id": request["previous_pair_id"],
        "pair_id": request["pair_id"],
        "pool_address": request["pool_address"],
        "amount_x": input_verification["amount_x"],
        "amount_y": input_verification["amount_y"],
        "network_cost_y_atomic": input_verification[
            "network_cost_y_atomic"
        ],
        "capital_quote": request["capital_quote"],
        "entry_cost_quote": request["entry_cost_quote"],
        "required_pair_cash_quote": request["required_pair_cash_quote"],
        "as_of": input_verification["as_of"],
        "lookback_observations": input_verification[
            "lookback_observations"
        ],
        "half_widths": input_verification["half_widths"],
        "center_offsets": input_verification["center_offsets"],
        "strategies": input_verification["strategies"],
        "max_share_bps": input_verification["max_share_bps"],
        "favor_x_in_active_bin": input_verification[
            "favor_x_in_active_bin"
        ],
        "near_liquidity_radius": input_verification[
            "near_liquidity_radius"
        ],
        "risk_lambda": input_verification["risk_lambda"],
        "min_positive_excess_probability": input_verification[
            "min_positive_excess_probability"
        ],
        "min_range_survival_probability": input_verification[
            "min_range_survival_probability"
        ],
        "min_score_bps": input_verification["min_score_bps"],
        "incumbent_position_id": request["incumbent_position_id"],
        "challenger_position_id": request["challenger_position_id"],
        "incumbent_event_key": request["incumbent_event_key"],
        "challenger_event_key": request["challenger_event_key"],
        "approver_principal": fresh_verification["approver_principal"],
        "approval_id": fresh_verification["approval_id"],
        "authorization_expires_at": fresh_verification["expires_at"],
        "decision_observed_at": candidate_report.decision_observed_at,
        "candidate_frame_sha256": _sha256_bytes(
            _canonical_bytes(candidate_record)
        ),
        "incumbent_inference_sha256": _sha256_bytes(
            _canonical_bytes(incumbent_inference_record)
        ),
        "challenger_inference_sha256": _sha256_bytes(
            _canonical_bytes(challenger_inference_record)
        ),
        "incumbent_choice_sha256": _sha256_bytes(
            _canonical_bytes(incumbent_choice_record)
        ),
        "challenger_choice_sha256": _sha256_bytes(
            _canonical_bytes(challenger_choice_record)
        ),
        "incumbent_preflight_sha256": _hash_record(incumbent_preflight),
        "challenger_preflight_sha256": _hash_record(challenger_preflight),
        "incumbent_choice": incumbent_choice_record,
        "challenger_choice": challenger_choice_record,
        "fresh_account_readiness_matches_saved": True,
        "fresh_request_matches_saved": True,
        "fresh_authorization_matches_saved": True,
        "human_repeat_rollover_paired_paper_entry_authorization_verified": True,
        "account_ready": True,
        "zero_open_positions_verified": True,
        "previous_pair_closed_verified": True,
        "cycle_model_binding_valid": True,
        "model_inference_readiness_verified": True,
        "same_candidate_frame_verified": True,
        "same_decision_snapshot_verified": True,
        "equal_capital_verified": True,
        "counterfactual_preflight_verified": True,
        "no_lookahead_verified": True,
        "paper_pair_entry_execution_readiness_ready": True,
        "readiness_only": True,
        "requires_immediate_one_shot_pair_executor": True,
        "post_pair_audit_required": True,
        "paper_pair_entry_authorized": False,
        "paper_pair_entry_executed": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "readiness_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paired_ml_paper_repeat_rollover_entry_execution_readiness(
        report
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freshly revalidate one signed repeat paired ML PAPER entry. "
            "The tool rebuilds repeat account/request/signature state, then "
            "runs both models against one exact no-lookahead candidate frame "
            "and re-preflights both chain choices. It opens no positions."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--final-evaluation", required=True)
    parser.add_argument("--rollover-entry-inputs", required=True)
    parser.add_argument("--saved-account-readiness", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--saved-signed-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_phase8_paired_ml_paper_repeat_rollover_entry_execution_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        final_evaluation_path=args.final_evaluation,
        rollover_entry_input_path=args.rollover_entry_inputs,
        saved_account_readiness_path=args.saved_account_readiness,
        request_path=args.request,
        saved_signed_authorization_verification_path=(
            args.saved_signed_verification
        ),
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=(
            args.expected_allowed_signers_sha256
        ),
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
