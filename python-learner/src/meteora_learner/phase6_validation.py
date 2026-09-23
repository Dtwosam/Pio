from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from .phase_promotion import PHASE5, PHASE5_EVIDENCE_TYPE
from .storage import Storage


@dataclass(frozen=True)
class Phase6PromotionCriteria:
    min_passed_enter_intents: int = 10
    min_distinct_pools: int = 2
    min_blocked_intents: int = 2
    max_postsimulation_intents: int = 0

    def validate(self) -> None:
        if self.min_passed_enter_intents < 1:
            raise ValueError("min_passed_enter_intents must be positive")
        if self.min_distinct_pools < 1:
            raise ValueError("min_distinct_pools must be positive")
        if self.min_blocked_intents < 0:
            raise ValueError("min_blocked_intents cannot be negative")
        if self.max_postsimulation_intents < 0:
            raise ValueError(
                "max_postsimulation_intents cannot be negative"
            )


@dataclass(frozen=True)
class Phase6PromotionReport:
    phase5_promoted: bool
    execution_db: str
    passed_enter_intents: int
    distinct_pools: int
    blocked_intents: int
    postsimulation_intents: int
    invalid_passed_intents: int
    distinct_authorized_wallets: tuple[str, ...]
    criteria: Phase6PromotionCriteria
    promotion_ready: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _json_object(raw: Any, *, field: str) -> dict[str, Any]:
    if raw is None:
        raise ValueError(f"{field} is missing")
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _accepted(value: dict[str, Any]) -> bool:
    return value.get("accepted") is True


def _passed_intent_valid(row: sqlite3.Row) -> tuple[bool, str | None]:
    try:
        risk = _json_object(row["risk_json"], field="risk_json")
        guard = _json_object(
            row["transaction_guard_json"],
            field="transaction_guard_json",
        )
        wallet = _json_object(
            row["wallet_authorization_json"],
            field="wallet_authorization_json",
        )
        prepared = _json_object(
            row["prepared_transaction_json"],
            field="prepared_transaction_json",
        )
        final_simulation = _json_object(
            row["final_simulation_json"],
            field="final_simulation_json",
        )
    except ValueError:
        return False, None

    wallet_pubkey = str(wallet.get("wallet_pubkey") or "")
    fee_payer = str(guard.get("fee_payer") or "")
    authorized_payer = str(wallet.get("transaction_fee_payer") or "")
    valid = (
        _accepted(risk)
        and _accepted(guard)
        and _accepted(wallet)
        and prepared.get("signatures_all_default") is True
        and final_simulation.get("succeeded") is True
        and bool(wallet_pubkey)
        and wallet_pubkey == fee_payer
        and wallet_pubkey == authorized_payer
    )
    return valid, wallet_pubkey or None


def evaluate_phase6_promotion(
    storage: Storage,
    *,
    execution_db: str | Path,
    criteria: Phase6PromotionCriteria = Phase6PromotionCriteria(),
) -> Phase6PromotionReport:
    criteria.validate()
    path = Path(execution_db)
    if not path.is_absolute():
        raise ValueError("execution_db must be an absolute path")
    if not path.exists():
        raise ValueError(f"execution_db does not exist: {path}")

    phase5_promoted = storage.phase_is_promoted(
        PHASE5,
        evidence_type=PHASE5_EVIDENCE_TYPE,
    )

    uri = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ValueError(f"cannot open execution_db read-only: {path}") from exc
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT decision_id, mode, action, pool_address, status,
                   risk_json, transaction_guard_json,
                   wallet_authorization_json,
                   prepared_transaction_json,
                   final_simulation_json
            FROM execution_intents
            ORDER BY created_at_unix ASC, decision_id ASC
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(
            "execution_db is missing the Phase 6 execution_intents schema"
        ) from exc
    finally:
        conn.close()

    passed_enter_intents = 0
    blocked_intents = 0
    postsimulation_intents = 0
    invalid_passed_intents = 0
    pools: set[str] = set()
    wallets: set[str] = set()

    for row in rows:
        mode = str(row["mode"])
        action = str(row["action"])
        status = str(row["status"])
        if mode != "LIVE":
            continue

        if status in {"REJECTED", "SIMULATION_FAILED"}:
            blocked_intents += 1
            continue

        if status in {"SIGNING", "SENT", "CONFIRMED", "FAILED"}:
            postsimulation_intents += 1
            continue

        if status != "SIMULATION_PASSED" or action != "ENTER":
            continue

        valid, wallet = _passed_intent_valid(row)
        if not valid:
            invalid_passed_intents += 1
            continue
        passed_enter_intents += 1
        pools.add(str(row["pool_address"]))
        if wallet is not None:
            wallets.add(wallet)

    reasons: list[str] = []
    if not phase5_promoted:
        reasons.append("Phase 5 must be persistently promoted before Phase 6")
    if passed_enter_intents < criteria.min_passed_enter_intents:
        reasons.append(
            f"validated LIVE ENTER intents {passed_enter_intents} are below "
            f"{criteria.min_passed_enter_intents}"
        )
    if len(pools) < criteria.min_distinct_pools:
        reasons.append(
            f"distinct validated pools {len(pools)} are below "
            f"{criteria.min_distinct_pools}"
        )
    if blocked_intents < criteria.min_blocked_intents:
        reasons.append(
            f"blocked-path intents {blocked_intents} are below "
            f"{criteria.min_blocked_intents}"
        )
    if postsimulation_intents > criteria.max_postsimulation_intents:
        reasons.append(
            f"post-simulation execution intents {postsimulation_intents} "
            f"exceed {criteria.max_postsimulation_intents}"
        )
    if invalid_passed_intents:
        reasons.append(
            f"{invalid_passed_intents} SIMULATION_PASSED ENTER intent(s) "
            "lack complete accepted presign evidence"
        )
    if len(wallets) != 1:
        reasons.append(
            f"validated corpus must use exactly one executor wallet; "
            f"observed {len(wallets)}"
        )

    return Phase6PromotionReport(
        phase5_promoted=phase5_promoted,
        execution_db=str(path),
        passed_enter_intents=passed_enter_intents,
        distinct_pools=len(pools),
        blocked_intents=blocked_intents,
        postsimulation_intents=postsimulation_intents,
        invalid_passed_intents=invalid_passed_intents,
        distinct_authorized_wallets=tuple(sorted(wallets)),
        criteria=criteria,
        promotion_ready=not reasons,
        reasons=tuple(reasons),
    )
