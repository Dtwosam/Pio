from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from .research_store import ResearchStore


@dataclass(frozen=True)
class PoolSafetyConfig:
    min_tvl_usd: float = 50_000.0
    min_volume_24h_usd: float = 10_000.0
    min_pool_age_hours: float = 24.0
    min_chain_observations: int = 12
    max_dynamic_fee_pct: float | None = 5.0
    max_pool_snapshot_age_seconds: int | None = 900
    require_standard_spl: bool = True
    require_not_blacklisted: bool = True

    def __post_init__(self) -> None:
        if self.min_tvl_usd < 0:
            raise ValueError("min_tvl_usd cannot be negative")
        if self.min_volume_24h_usd < 0:
            raise ValueError("min_volume_24h_usd cannot be negative")
        if self.min_pool_age_hours < 0:
            raise ValueError("min_pool_age_hours cannot be negative")
        if self.min_chain_observations < 0:
            raise ValueError("min_chain_observations cannot be negative")
        if self.max_dynamic_fee_pct is not None and self.max_dynamic_fee_pct < 0:
            raise ValueError("max_dynamic_fee_pct cannot be negative")
        if (
            self.max_pool_snapshot_age_seconds is not None
            and self.max_pool_snapshot_age_seconds < 0
        ):
            raise ValueError("max_pool_snapshot_age_seconds cannot be negative")


@dataclass(frozen=True)
class PoolSafetyAssessment:
    pool_address: str
    name: str | None
    token_x_symbol: str | None
    token_y_symbol: str | None
    accepted: bool
    rejection_reasons: tuple[str, ...]
    tvl_usd: float | None
    volume_24h_usd: float | None
    fees_24h_usd: float | None
    dynamic_fee_pct: float | None
    snapshot_age_seconds: int | None
    pool_age_hours: float | None
    chain_observations: int
    standard_spl: bool | None
    is_blacklisted: bool | None


@dataclass(frozen=True)
class PoolSafetyReport:
    as_of: str
    pools_seen: int
    pools_accepted: int
    pools_rejected: int
    config: PoolSafetyConfig
    assessments: tuple[PoolSafetyAssessment, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_iso(value: str) -> datetime:
    candidate = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pool_created_at(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = int(value)
    if raw > 10_000_000_000:
        raw //= 1000
    try:
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def screen_pool_universe(
    database_path: str,
    *,
    config: PoolSafetyConfig = PoolSafetyConfig(),
    as_of: str | None = None,
) -> PoolSafetyReport:
    store = ResearchStore(database_path)
    pools = store.latest_pool_snapshots()
    if not pools:
        raise ValueError("no normalized pool snapshots available")

    if as_of is None:
        as_of_dt = max(_parse_iso(str(row["observed_at"])) for row in pools)
        as_of = as_of_dt.isoformat()
    else:
        as_of_dt = _parse_iso(as_of)

    assessments: list[PoolSafetyAssessment] = []
    for pool in pools:
        address = str(pool["address"])
        reasons: list[str] = []

        tvl = float(pool["tvl"]) if pool.get("tvl") is not None else None
        volume = (
            float(pool["volume_24h"])
            if pool.get("volume_24h") is not None
            else None
        )
        fees = (
            float(pool["fees_24h"])
            if pool.get("fees_24h") is not None
            else None
        )
        dynamic_fee = (
            float(pool["dynamic_fee_pct"])
            if pool.get("dynamic_fee_pct") is not None
            else None
        )

        snapshot_age_seconds: int | None = None
        observed_raw = pool.get("observed_at")
        if observed_raw is None:
            reasons.append("pool snapshot timestamp is unknown")
        else:
            observed_dt = _parse_iso(str(observed_raw))
            snapshot_age_seconds = int(
                (as_of_dt - observed_dt).total_seconds()
            )
            if snapshot_age_seconds < 0:
                reasons.append("pool snapshot is after evaluation time")
            elif (
                config.max_pool_snapshot_age_seconds is not None
                and snapshot_age_seconds
                > config.max_pool_snapshot_age_seconds
            ):
                reasons.append(
                    f"pool snapshot age {snapshot_age_seconds}s > allowed "
                    f"{config.max_pool_snapshot_age_seconds}s"
                )

        if tvl is None:
            reasons.append("TVL is unknown")
        elif tvl < config.min_tvl_usd:
            reasons.append(
                f"TVL {tvl:.2f} < required {config.min_tvl_usd:.2f}"
            )

        if volume is None:
            reasons.append("24h volume is unknown")
        elif volume < config.min_volume_24h_usd:
            reasons.append(
                f"24h volume {volume:.2f} < required "
                f"{config.min_volume_24h_usd:.2f}"
            )

        blacklisted: bool | None
        if pool.get("is_blacklisted") is None:
            blacklisted = None
        else:
            blacklisted = bool(pool["is_blacklisted"])
        if config.require_not_blacklisted:
            if blacklisted is None:
                reasons.append("blacklist status is unknown")
            elif blacklisted:
                reasons.append("pool is blacklisted")

        created = _pool_created_at(pool.get("pool_created_at"))
        age_hours: float | None = None
        if created is None:
            reasons.append("pool creation time is unknown")
        else:
            age_hours = (as_of_dt - created).total_seconds() / 3600.0
            if age_hours < 0:
                reasons.append("pool creation time is after evaluation time")
            elif age_hours < config.min_pool_age_hours:
                reasons.append(
                    f"pool age {age_hours:.2f}h < required "
                    f"{config.min_pool_age_hours:.2f}h"
                )

        if config.max_dynamic_fee_pct is not None:
            if dynamic_fee is None:
                reasons.append("dynamic fee is unknown")
            elif dynamic_fee > config.max_dynamic_fee_pct:
                reasons.append(
                    f"dynamic fee {dynamic_fee:.6f}% > allowed "
                    f"{config.max_dynamic_fee_pct:.6f}%"
                )

        chain = store.latest_chain_pool_snapshot(address)
        standard_spl: bool | None = None
        if chain is not None:
            x_program = chain.get("token_x_program")
            y_program = chain.get("token_y_program")
            if x_program is not None and y_program is not None:
                standard_spl = (
                    str(x_program) == STANDARD_SPL_TOKEN_PROGRAM
                    and str(y_program) == STANDARD_SPL_TOKEN_PROGRAM
                )
        if config.require_standard_spl:
            if standard_spl is None:
                reasons.append("token program support is unknown")
            elif not standard_spl:
                reasons.append("pool is not standard-SPL on both sides")

        chain_observations = store.chain_observation_count(address)
        if chain_observations < config.min_chain_observations:
            reasons.append(
                f"chain observations {chain_observations} < required "
                f"{config.min_chain_observations}"
            )

        assessments.append(
            PoolSafetyAssessment(
                pool_address=address,
                name=str(pool["name"]) if pool.get("name") is not None else None,
                token_x_symbol=(
                    str(pool["token_x_symbol"])
                    if pool.get("token_x_symbol") is not None
                    else None
                ),
                token_y_symbol=(
                    str(pool["token_y_symbol"])
                    if pool.get("token_y_symbol") is not None
                    else None
                ),
                accepted=not reasons,
                rejection_reasons=tuple(reasons),
                tvl_usd=tvl,
                volume_24h_usd=volume,
                fees_24h_usd=fees,
                dynamic_fee_pct=dynamic_fee,
                snapshot_age_seconds=snapshot_age_seconds,
                pool_age_hours=age_hours,
                chain_observations=chain_observations,
                standard_spl=standard_spl,
                is_blacklisted=blacklisted,
            )
        )

    return PoolSafetyReport(
        as_of=as_of,
        pools_seen=len(assessments),
        pools_accepted=sum(item.accepted for item in assessments),
        pools_rejected=sum(not item.accepted for item in assessments),
        config=config,
        assessments=tuple(assessments),
    )
