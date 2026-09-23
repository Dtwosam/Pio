from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .mint_snapshot_lineage import (
    MINT_SOURCE_COLUMNS,
    POOL_SOURCE_COLUMNS,
    mint_risk_mint_source_record,
    mint_risk_pool_source_record,
    mint_risk_source_sha256,
)
from .phase8_validation import audit_persisted_phase8_promotion
from .storage import Storage


SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
DEFAULT_PUBKEY = "11111111111111111111111111111111"
MINT_RISK_EVIDENCE_TYPE = "PHASE9_MINT_RISK_V1"


@dataclass(frozen=True)
class MintRiskCriteria:
    max_snapshot_age_seconds: int = 3600
    max_decimals: int = 12
    require_initialized: bool = True
    require_mint_authority_revoked: bool = True
    require_freeze_authority_revoked: bool = True
    allow_token_2022: bool = True
    allow_token_2022_extension_data: bool = False
    include_reward_mints: bool = True

    def __post_init__(self) -> None:
        if self.max_snapshot_age_seconds < 0:
            raise ValueError("max_snapshot_age_seconds cannot be negative")
        if not 0 <= self.max_decimals <= 255:
            raise ValueError("max_decimals must be between 0 and 255")


@dataclass(frozen=True)
class MintRiskAssessment:
    mint_address: str
    roles: tuple[str, ...]
    mint_snapshot_id: int | None
    mint_snapshot_sha256: str | None
    observed_at: str | None
    age_seconds: int | None
    expected_program: str | None
    observed_program: str | None
    initialized: bool | None
    mint_authority_revoked: bool | None
    freeze_authority_revoked: bool | None
    decimals: int | None
    token_2022_extension_data_len: int | None
    accepted: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PoolMintRiskReport:
    pool_address: str
    pool_snapshot_id: int
    pool_snapshot_sha256: str
    as_of: str
    phase8_promoted: bool
    research_only: bool
    policy_actionable: bool
    status: str
    criteria: MintRiskCriteria
    mints_required: int
    mints_accepted: int
    research_qualified: bool
    reasons: tuple[str, ...]
    assessments: tuple[MintRiskAssessment, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("mint snapshot timestamps must include timezone")
    return parsed.astimezone(timezone.utc)


def research_pool_mint_risk(
    storage: Storage,
    *,
    pool_address: str,
    criteria: MintRiskCriteria = MintRiskCriteria(),
    as_of: str | None = None,
) -> PoolMintRiskReport:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    now = _parse_time(as_of) if as_of else datetime.now(timezone.utc)
    as_of_text = now.isoformat()
    phase8_audit = audit_persisted_phase8_promotion(storage)
    phase8_promoted = phase8_audit.current

    with storage.connect() as conn:
        if as_of is None:
            pool = conn.execute(
                """
                SELECT id, token_x_mint, token_y_mint,
                       token_x_program, token_y_program,
                       reward_mint_0, reward_mint_1
                FROM chain_pool_snapshots
                WHERE pool_address = ?
                ORDER BY julianday(observed_at) DESC, id DESC
                LIMIT 1
                """,
                (pool_address,),
            ).fetchone()
        else:
            pool = conn.execute(
                """
                SELECT id, token_x_mint, token_y_mint,
                       token_x_program, token_y_program,
                       reward_mint_0, reward_mint_1
                FROM chain_pool_snapshots
                WHERE pool_address = ?
                  AND julianday(observed_at) <= julianday(?)
                ORDER BY julianday(observed_at) DESC, id DESC
                LIMIT 1
                """,
                (pool_address, as_of),
            ).fetchone()
        if pool is None:
            raise ValueError(f"no chain pool snapshot for {pool_address}")

        pool_source = conn.execute(
            f"""
            SELECT {", ".join(POOL_SOURCE_COLUMNS)}
            FROM chain_pool_snapshots
            WHERE id = ?
            """,
            (int(pool[0]),),
        ).fetchone()
        if pool_source is None:
            raise ValueError("selected pool snapshot disappeared")
        pool_snapshot_sha256 = mint_risk_source_sha256(
            mint_risk_pool_source_record(pool_source)
        )

        required: dict[str, dict[str, Any]] = {}
        for role, mint, program in (
            ("TOKEN_X", pool[1], pool[3]),
            ("TOKEN_Y", pool[2], pool[4]),
        ):
            required.setdefault(str(mint), {"roles": [], "programs": set()})
            required[str(mint)]["roles"].append(role)
            if program is not None:
                required[str(mint)]["programs"].add(str(program))

        if criteria.include_reward_mints:
            for index, mint in enumerate((pool[5], pool[6])):
                if mint is None or str(mint) == DEFAULT_PUBKEY:
                    continue
                required.setdefault(
                    str(mint), {"roles": [], "programs": set()}
                )
                required[str(mint)]["roles"].append(f"REWARD_{index}")

        assessments: list[MintRiskAssessment] = []
        for mint, metadata in sorted(required.items()):
            if as_of is None:
                row = conn.execute(
                    """
                    SELECT id, observed_at, token_program, decimals,
                           is_initialized, mint_authority,
                           freeze_authority,
                           token_2022_extension_data_len
                    FROM token_mint_snapshots
                    WHERE mint_address = ?
                    ORDER BY julianday(observed_at) DESC, id DESC
                    LIMIT 1
                    """,
                    (mint,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id, observed_at, token_program, decimals,
                           is_initialized, mint_authority,
                           freeze_authority,
                           token_2022_extension_data_len
                    FROM token_mint_snapshots
                    WHERE mint_address = ?
                      AND julianday(observed_at) <= julianday(?)
                    ORDER BY julianday(observed_at) DESC, id DESC
                    LIMIT 1
                    """,
                    (mint, as_of),
                ).fetchone()

            reasons: list[str] = []
            expected_programs = sorted(metadata["programs"])
            expected_program = (
                expected_programs[0]
                if len(expected_programs) == 1
                else None
            )
            if len(expected_programs) > 1:
                reasons.append("pool snapshots disagree on token program")

            if row is None:
                assessments.append(
                    MintRiskAssessment(
                        mint_address=mint,
                        roles=tuple(metadata["roles"]),
                        mint_snapshot_id=None,
                        mint_snapshot_sha256=None,
                        observed_at=None,
                        age_seconds=None,
                        expected_program=expected_program,
                        observed_program=None,
                        initialized=None,
                        mint_authority_revoked=None,
                        freeze_authority_revoked=None,
                        decimals=None,
                        token_2022_extension_data_len=None,
                        accepted=False,
                        reasons=("mint snapshot is missing",),
                    )
                )
                continue

            mint_snapshot_id = int(row[0])
            mint_source = conn.execute(
                f"""
                SELECT {", ".join(MINT_SOURCE_COLUMNS)}
                FROM token_mint_snapshots
                WHERE id = ?
                """,
                (mint_snapshot_id,),
            ).fetchone()
            if mint_source is None:
                raise ValueError("selected mint snapshot disappeared")
            mint_snapshot_sha256 = mint_risk_source_sha256(
                mint_risk_mint_source_record(mint_source)
            )
            observed_at = str(row[1])
            age = int((now - _parse_time(observed_at)).total_seconds())
            program = str(row[2])
            decimals = int(row[3])
            initialized = bool(row[4])
            mint_revoked = row[5] is None
            freeze_revoked = row[6] is None
            extension_len = int(row[7])

            if age < 0:
                reasons.append("mint snapshot is after evaluation time")
            elif age > criteria.max_snapshot_age_seconds:
                reasons.append(
                    f"mint snapshot age {age}s exceeds "
                    f"{criteria.max_snapshot_age_seconds}s"
                )
            if program not in {SPL_TOKEN_PROGRAM, TOKEN_2022_PROGRAM}:
                reasons.append("unsupported token program")
            if expected_program is not None and program != expected_program:
                reasons.append("mint token program does not match pool")
            if program == TOKEN_2022_PROGRAM and not criteria.allow_token_2022:
                reasons.append("Token-2022 is not allowed by criteria")
            if (
                program == TOKEN_2022_PROGRAM
                and extension_len > 0
                and not criteria.allow_token_2022_extension_data
            ):
                reasons.append(
                    "Token-2022 extension data requires explicit allowance"
                )
            if criteria.require_initialized and not initialized:
                reasons.append("mint is not initialized")
            if criteria.require_mint_authority_revoked and not mint_revoked:
                reasons.append("mint authority is still active")
            if criteria.require_freeze_authority_revoked and not freeze_revoked:
                reasons.append("freeze authority is still active")
            if decimals > criteria.max_decimals:
                reasons.append(
                    f"mint decimals {decimals} exceed {criteria.max_decimals}"
                )

            assessments.append(
                MintRiskAssessment(
                    mint_address=mint,
                    roles=tuple(metadata["roles"]),
                    mint_snapshot_id=mint_snapshot_id,
                    mint_snapshot_sha256=mint_snapshot_sha256,
                    observed_at=observed_at,
                    age_seconds=age,
                    expected_program=expected_program,
                    observed_program=program,
                    initialized=initialized,
                    mint_authority_revoked=mint_revoked,
                    freeze_authority_revoked=freeze_revoked,
                    decimals=decimals,
                    token_2022_extension_data_len=extension_len,
                    accepted=not reasons,
                    reasons=tuple(reasons),
                )
            )

    failed = [item for item in assessments if not item.accepted]
    reasons: list[str] = []
    if not phase8_promoted:
        reasons.append(
            "Phase 8 promotion must still be current before mint-risk research can qualify"
        )
        reasons.extend(
            f"Phase 8 currentness: {reason}"
            for reason in phase8_audit.reasons
        )
    if failed:
        reasons.append(f"{len(failed)} required mint(s) failed risk checks")
    qualified = not reasons
    if not phase8_promoted:
        status = "RESEARCH_ONLY_PHASE8_BLOCKED"
    elif qualified:
        status = "QUALIFIED_RESEARCH"
    else:
        status = "NOT_QUALIFIED"

    return PoolMintRiskReport(
        pool_address=pool_address,
        pool_snapshot_id=int(pool[0]),
        pool_snapshot_sha256=pool_snapshot_sha256,
        as_of=as_of_text,
        phase8_promoted=phase8_promoted,
        research_only=True,
        policy_actionable=False,
        status=status,
        criteria=criteria,
        mints_required=len(assessments),
        mints_accepted=sum(item.accepted for item in assessments),
        research_qualified=qualified,
        reasons=tuple(reasons),
        assessments=tuple(assessments),
    )


def persist_pool_mint_risk(
    storage: Storage,
    *,
    report: PoolMintRiskReport,
) -> int:
    return storage.save_advanced_edge_evidence(
        edge_type=MINT_RISK_EVIDENCE_TYPE,
        pool_address=report.pool_address,
        as_of=report.as_of,
        status=report.status,
        qualified=report.research_qualified,
        evidence=report.to_record(),
    )
