from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from .phase8_validation import audit_persisted_phase8_promotion
from .storage import Storage


WALLET_FLOW_EVIDENCE_TYPE = "PHASE9_WALLET_FLOW_V1"


@dataclass(frozen=True)
class WalletFlowCriteria:
    lookback_events: int = 500
    min_events: int = 20
    min_unique_users: int = 5
    max_top_user_share_bps: int = 4_000

    def __post_init__(self) -> None:
        if self.lookback_events < 1:
            raise ValueError("lookback_events must be positive")
        if self.min_events < 1:
            raise ValueError("min_events must be positive")
        if self.min_unique_users < 1:
            raise ValueError("min_unique_users must be positive")
        if not 1 <= self.max_top_user_share_bps <= 10_000:
            raise ValueError(
                "max_top_user_share_bps must be between 1 and 10000"
            )


@dataclass(frozen=True)
class WalletActivity:
    user_address: str
    events: int
    activity_usd: float
    activity_share_bps: int


@dataclass(frozen=True)
class WalletFlowResearchReport:
    pool_address: str
    as_of: str | None
    phase8_promoted: bool
    status: str
    research_only: bool
    policy_actionable: bool
    events_used: int
    unique_users: int
    repeat_users: int
    repeat_user_rate: float | None
    total_activity_usd: float
    top_user_share_bps: int | None
    top_three_share_bps: int | None
    hhi_bps: int | None
    classified_add_events: int
    classified_remove_events: int
    neutral_events: int
    unclassified_events: int
    classified_add_usd: float
    classified_remove_usd: float
    classified_net_add_usd: float
    direction_fidelity: str
    source_event_ids: tuple[int, ...]
    source_event_sha256: str
    criteria: WalletFlowCriteria
    research_qualified: bool
    reasons: tuple[str, ...]
    top_wallets: tuple[WalletActivity, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def wallet_flow_source_sha256(
    records: list[dict[str, Any]],
) -> str:
    canonical = json.dumps(
        records,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _usd(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid position event total_usd: {value}") from exc
    if not parsed.is_finite():
        raise ValueError("position event total_usd must be finite")
    return abs(parsed)


def _direction(event_type: str) -> str:
    value = event_type.upper()
    if any(
        token in value
        for token in ("ADD", "DEPOSIT", "INCREASE", "OPEN")
    ):
        return "ADD"
    if any(
        token in value
        for token in ("REMOVE", "WITHDRAW", "DECREASE", "CLOSE")
    ):
        return "REMOVE"
    if any(token in value for token in ("CLAIM", "FEE", "REWARD")):
        return "NEUTRAL"
    return "UNKNOWN"


def research_wallet_flow(
    storage: Storage,
    *,
    pool_address: str,
    criteria: WalletFlowCriteria = WalletFlowCriteria(),
    as_of: str | None = None,
) -> WalletFlowResearchReport:
    if not pool_address.strip():
        raise ValueError("pool_address is required")

    phase8_audit = audit_persisted_phase8_promotion(storage)
    phase8_promoted = phase8_audit.current

    with storage.connect() as conn:
        if as_of is None:
            rows = conn.execute(
                """
                SELECT id, created_at, user_address, event_type, total_usd,
                       signature, ix_index, position_address
                FROM position_event_history
                WHERE pool_address = ?
                ORDER BY julianday(created_at) DESC, id DESC
                LIMIT ?
                """,
                (pool_address, criteria.lookback_events),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, created_at, user_address, event_type, total_usd,
                       signature, ix_index, position_address
                FROM position_event_history
                WHERE pool_address = ?
                  AND julianday(created_at) <= julianday(?)
                ORDER BY julianday(created_at) DESC, id DESC
                LIMIT ?
                """,
                (
                    pool_address,
                    as_of,
                    criteria.lookback_events,
                ),
            ).fetchall()

    per_user: dict[str, tuple[int, Decimal]] = {}
    add_events = 0
    remove_events = 0
    neutral_events = 0
    unknown_events = 0
    add_usd = Decimal(0)
    remove_usd = Decimal(0)

    source_records = [
        {
            "id": int(row[0]),
            "created_at": str(row[1]),
            "user_address": str(row[2]),
            "event_type": str(row[3]),
            "total_usd": str(row[4]),
            "signature": str(row[5]),
            "ix_index": int(row[6]),
            "position_address": str(row[7]),
        }
        for row in rows
    ]
    source_event_ids = tuple(
        int(record["id"]) for record in source_records
    )
    source_event_sha256 = wallet_flow_source_sha256(source_records)

    for row in rows:
        user = str(row[2])
        event_type = str(row[3])
        activity = _usd(row[4])
        count, total = per_user.get(user, (0, Decimal(0)))
        per_user[user] = (count + 1, total + activity)

        direction = _direction(event_type)
        if direction == "ADD":
            add_events += 1
            add_usd += activity
        elif direction == "REMOVE":
            remove_events += 1
            remove_usd += activity
        elif direction == "NEUTRAL":
            neutral_events += 1
        else:
            unknown_events += 1

    total_activity = sum(
        (total for _, total in per_user.values()),
        Decimal(0),
    )
    wallets = sorted(
        per_user.items(),
        key=lambda item: (
            item[1][1],
            item[1][0],
            item[0],
        ),
        reverse=True,
    )

    wallet_items: list[WalletActivity] = []
    shares_bps: list[int] = []
    for user, (count, activity) in wallets:
        share = (
            int(activity * Decimal(10_000) / total_activity)
            if total_activity > 0
            else 0
        )
        shares_bps.append(share)
        wallet_items.append(
            WalletActivity(
                user_address=user,
                events=count,
                activity_usd=float(activity),
                activity_share_bps=share,
            )
        )

    unique_users = len(wallet_items)
    repeat_users = sum(item.events >= 2 for item in wallet_items)
    repeat_rate = (
        repeat_users / unique_users
        if unique_users
        else None
    )
    top_share = shares_bps[0] if shares_bps else None
    top_three = (
        sum(shares_bps[:3]) if shares_bps else None
    )
    hhi = (
        int(
            round(
                sum(
                    (share / 10_000.0) ** 2
                    for share in shares_bps
                )
                * 10_000
            )
        )
        if shares_bps
        else None
    )

    reasons: list[str] = []
    checks = (
        (
            len(rows) >= criteria.min_events,
            f"events {len(rows)} are below {criteria.min_events}",
        ),
        (
            unique_users >= criteria.min_unique_users,
            f"unique users {unique_users} are below "
            f"{criteria.min_unique_users}",
        ),
        (
            top_share is not None
            and top_share <= criteria.max_top_user_share_bps,
            "top-user activity concentration exceeds configured maximum",
        ),
    )
    reasons.extend(message for passed, message in checks if not passed)
    if not phase8_promoted:
        reasons.append(
            "Phase 8 promotion must still be current before wallet-flow research can qualify"
        )
        reasons.extend(
            f"Phase 8 currentness: {reason}"
            for reason in phase8_audit.reasons
        )

    research_qualified = not reasons and phase8_promoted
    if len(rows) < criteria.min_events or unique_users < criteria.min_unique_users:
        status = "INSUFFICIENT_HISTORY"
    elif not phase8_promoted:
        status = "RESEARCH_ONLY_PHASE8_BLOCKED"
    elif reasons:
        status = "CONCENTRATED_FLOW"
    else:
        status = "RESEARCH_READY"

    if unknown_events == 0:
        direction_fidelity = "CLASSIFIED"
    elif unknown_events == len(rows):
        direction_fidelity = "UNCLASSIFIED"
    else:
        direction_fidelity = "PARTIAL"

    return WalletFlowResearchReport(
        pool_address=pool_address,
        as_of=as_of,
        phase8_promoted=phase8_promoted,
        status=status,
        research_only=True,
        policy_actionable=False,
        events_used=len(rows),
        unique_users=unique_users,
        repeat_users=repeat_users,
        repeat_user_rate=repeat_rate,
        total_activity_usd=float(total_activity),
        top_user_share_bps=top_share,
        top_three_share_bps=top_three,
        hhi_bps=hhi,
        classified_add_events=add_events,
        classified_remove_events=remove_events,
        neutral_events=neutral_events,
        unclassified_events=unknown_events,
        classified_add_usd=float(add_usd),
        classified_remove_usd=float(remove_usd),
        classified_net_add_usd=float(add_usd - remove_usd),
        direction_fidelity=direction_fidelity,
        source_event_ids=source_event_ids,
        source_event_sha256=source_event_sha256,
        criteria=criteria,
        research_qualified=research_qualified,
        reasons=tuple(reasons),
        top_wallets=tuple(wallet_items[:10]),
    )


def persist_wallet_flow_research(
    storage: Storage,
    *,
    report: WalletFlowResearchReport,
) -> int:
    return storage.save_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address=report.pool_address,
        as_of=report.as_of,
        status=report.status,
        qualified=report.research_qualified,
        evidence=report.to_record(),
    )
