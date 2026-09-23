from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable

from .mint_ingest import ingest_mint_snapshot
from .mint_risk import DEFAULT_PUBKEY, MintRiskCriteria
from .paper_chain_refresh import default_rust_manifest_path
from .storage import Storage


InspectMint = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class Phase9MintCaptureCriteria:
    target_pools: int = 2
    max_snapshot_age_seconds: int = 3600
    include_reward_mints: bool = True

    def validate(self) -> None:
        if self.target_pools < 1:
            raise ValueError("target_pools must be positive")
        if self.max_snapshot_age_seconds < 0:
            raise ValueError(
                "max_snapshot_age_seconds cannot be negative"
            )


@dataclass(frozen=True)
class Phase9MintCaptureCandidate:
    mint_address: str
    pools: tuple[str, ...]
    roles: tuple[str, ...]
    latest_snapshot_at: str | None
    age_seconds: int | None
    capture_required: bool
    reason: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9MintCapturePlan:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    as_of: str
    target_pools: int
    selected_pools: tuple[str, ...]
    selected_pool_count: int
    required_mints: int
    current_mints: int
    captures_required: int
    inputs_ready: bool
    criteria: Phase9MintCaptureCriteria
    candidates: tuple[Phase9MintCaptureCandidate, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9MintCaptureItem:
    mint_address: str
    status: str
    snapshot_id: int | None
    error: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9MintCaptureReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    as_of: str
    mints_attempted: int
    mints_captured: int
    mints_failed: int
    inputs_ready_before: bool
    inputs_ready_after: bool
    captures_remaining_after: int
    items: tuple[Phase9MintCaptureItem, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("mint capture timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def inspect_mint_with_rust(
    mint_address: str,
    *,
    rust_manifest_path: str | Path | None = None,
    rust_binary_path: str | Path | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    if not mint_address.strip():
        raise ValueError("mint_address is required")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if not (os.getenv("SOLANA_RPC_URL") or os.getenv("RPC_URL")):
        raise ValueError(
            "SOLANA_RPC_URL environment variable is required "
            "(RPC_URL is accepted as a compatibility fallback)"
        )

    configured_binary = (
        rust_binary_path
        if rust_binary_path is not None
        else os.getenv("PIO_RUST_EXECUTOR_BIN")
    )
    if configured_binary:
        binary = Path(configured_binary)
        if not binary.exists():
            raise ValueError(f"Rust executor binary not found: {binary}")
        command = [
            str(binary),
            "inspect-mint-env",
            mint_address,
        ]
    else:
        manifest = Path(rust_manifest_path or default_rust_manifest_path())
        if not manifest.exists():
            raise ValueError(f"Rust manifest not found: {manifest}")
        command = [
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            str(manifest),
            "--",
            "inspect-mint-env",
            mint_address,
        ]

    process = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip()
        raise RuntimeError(
            f"Rust inspect-mint failed for {mint_address}: {detail[:2000]}"
        )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Rust inspect-mint returned invalid JSON for {mint_address}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Rust inspect-mint output must be a JSON object")
    return payload


def _selected_pools(
    storage: Storage,
    limit: int,
) -> tuple[str, ...]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT pool_address, COUNT(*) AS observations
            FROM chain_pool_snapshots
            WHERE pool_address IS NOT NULL
              AND TRIM(pool_address) != ''
            GROUP BY pool_address
            ORDER BY observations DESC, pool_address ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _pool_mints(
    storage: Storage,
    pool_address: str,
    *,
    include_reward_mints: bool,
) -> tuple[tuple[str, str], ...]:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT token_x_mint, token_y_mint,
                   reward_mint_0, reward_mint_1
            FROM chain_pool_snapshots
            WHERE pool_address = ?
            ORDER BY julianday(observed_at) DESC, id DESC
            LIMIT 1
            """,
            (pool_address,),
        ).fetchone()
    if row is None:
        return ()

    values: list[tuple[str, str]] = []
    for role, raw in (
        ("TOKEN_X", row[0]),
        ("TOKEN_Y", row[1]),
        ("REWARD_0", row[2]),
        ("REWARD_1", row[3]),
    ):
        if role.startswith("REWARD") and not include_reward_mints:
            continue
        if raw is None:
            continue
        mint = str(raw).strip()
        if not mint or mint == DEFAULT_PUBKEY:
            continue
        values.append((mint, role))
    return tuple(values)


def _latest_mint_snapshot(
    storage: Storage,
    mint_address: str,
    as_of: str,
) -> tuple[str, int] | None:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT observed_at, id
            FROM token_mint_snapshots
            WHERE mint_address = ?
              AND julianday(observed_at) <= julianday(?)
            ORDER BY julianday(observed_at) DESC, id DESC
            LIMIT 1
            """,
            (mint_address, as_of),
        ).fetchone()
    if row is None:
        return None
    return str(row[0]), int(row[1])


def build_phase9_mint_capture_plan(
    storage: Storage,
    *,
    criteria: Phase9MintCaptureCriteria = Phase9MintCaptureCriteria(),
    as_of: str | None = None,
) -> Phase9MintCapturePlan:
    criteria.validate()
    now = (
        _parse_time(as_of)
        if as_of is not None
        else datetime.now(timezone.utc)
    )
    as_of_text = now.isoformat()
    pools = _selected_pools(storage, criteria.target_pools)

    required: dict[str, dict[str, set[str]]] = {}
    for pool in pools:
        for mint, role in _pool_mints(
            storage,
            pool,
            include_reward_mints=criteria.include_reward_mints,
        ):
            item = required.setdefault(
                mint,
                {"pools": set(), "roles": set()},
            )
            item["pools"].add(pool)
            item["roles"].add(role)

    candidates: list[Phase9MintCaptureCandidate] = []
    current = 0
    for mint in sorted(required):
        latest = _latest_mint_snapshot(storage, mint, as_of_text)
        latest_at = latest[0] if latest is not None else None
        age = (
            int((now - _parse_time(latest_at)).total_seconds())
            if latest_at is not None
            else None
        )
        if latest_at is None:
            capture_required = True
            reason = "mint snapshot is missing"
        elif age is not None and age < 0:
            capture_required = True
            reason = "mint snapshot is after evaluation time"
        elif (
            age is not None
            and age > criteria.max_snapshot_age_seconds
        ):
            capture_required = True
            reason = (
                f"mint snapshot age {age}s exceeds "
                f"{criteria.max_snapshot_age_seconds}s"
            )
        else:
            capture_required = False
            reason = None
            current += 1

        candidates.append(
            Phase9MintCaptureCandidate(
                mint_address=mint,
                pools=tuple(sorted(required[mint]["pools"])),
                roles=tuple(sorted(required[mint]["roles"])),
                latest_snapshot_at=latest_at,
                age_seconds=age,
                capture_required=capture_required,
                reason=reason,
            )
        )

    reasons: list[str] = []
    if len(pools) < criteria.target_pools:
        reasons.append(
            f"chain-observed pools {len(pools)} are below "
            f"{criteria.target_pools}"
        )
    if not required and pools:
        reasons.append(
            "selected chain pools do not expose any usable mint addresses"
        )

    captures_required = sum(
        item.capture_required for item in candidates
    )
    inputs_ready = (
        len(pools) >= criteria.target_pools
        and bool(required)
        and captures_required == 0
        and not reasons
    )

    return Phase9MintCapturePlan(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        as_of=as_of_text,
        target_pools=criteria.target_pools,
        selected_pools=pools,
        selected_pool_count=len(pools),
        required_mints=len(candidates),
        current_mints=current,
        captures_required=captures_required,
        inputs_ready=inputs_ready,
        criteria=criteria,
        candidates=tuple(candidates),
        reasons=tuple(reasons),
    )


def run_phase9_mint_capture(
    storage: Storage,
    *,
    criteria: Phase9MintCaptureCriteria = Phase9MintCaptureCriteria(),
    inspector: InspectMint | None = None,
    rust_manifest_path: str | Path | None = None,
    rust_binary_path: str | Path | None = None,
    timeout_seconds: int = 120,
    observed_at: str | None = None,
) -> Phase9MintCaptureReport:
    timestamp = (
        _parse_time(observed_at)
        if observed_at is not None
        else datetime.now(timezone.utc)
    )
    timestamp_text = timestamp.isoformat()
    before = build_phase9_mint_capture_plan(
        storage,
        criteria=criteria,
        as_of=timestamp_text,
    )

    if inspector is None:
        def inspect(mint: str) -> dict[str, Any]:
            return inspect_mint_with_rust(
                mint,
                rust_manifest_path=rust_manifest_path,
                rust_binary_path=rust_binary_path,
                timeout_seconds=timeout_seconds,
            )
    else:
        inspect = inspector

    captures = tuple(
        item for item in before.candidates
        if item.capture_required
    )
    items: list[Phase9MintCaptureItem] = []
    captured = 0
    failed = 0

    for candidate in captures:
        try:
            if candidate.latest_snapshot_at is not None:
                if timestamp <= _parse_time(candidate.latest_snapshot_at):
                    raise ValueError(
                        "mint capture observed_at must be newer than the "
                        "latest persisted mint snapshot"
                    )
            payload = inspect(candidate.mint_address)
            if str(payload.get("mint_address", "")).strip() != (
                candidate.mint_address
            ):
                raise ValueError(
                    "Rust inspector returned a different mint_address"
                )
            result = ingest_mint_snapshot(
                storage,
                payload,
                observed_at=timestamp_text,
            )
            captured += 1
            items.append(
                Phase9MintCaptureItem(
                    mint_address=candidate.mint_address,
                    status="CAPTURED",
                    snapshot_id=result.snapshot_id,
                    error=None,
                )
            )
        except Exception as exc:
            failed += 1
            items.append(
                Phase9MintCaptureItem(
                    mint_address=candidate.mint_address,
                    status="FAILED",
                    snapshot_id=None,
                    error=str(exc)[:2000],
                )
            )

    after = build_phase9_mint_capture_plan(
        storage,
        criteria=criteria,
        as_of=timestamp_text,
    )
    reasons: list[str] = []
    if before.inputs_ready:
        reasons.append("mint-risk input snapshots were already current")
    if failed:
        reasons.append(f"{failed} read-only mint capture(s) failed")
    if not after.inputs_ready:
        reasons.extend(after.reasons)
        if after.captures_required:
            reasons.append(
                f"{after.captures_required} mint capture(s) remain"
            )

    return Phase9MintCaptureReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        as_of=timestamp_text,
        mints_attempted=len(captures),
        mints_captured=captured,
        mints_failed=failed,
        inputs_ready_before=before.inputs_ready,
        inputs_ready_after=after.inputs_ready,
        captures_remaining_after=after.captures_required,
        items=tuple(items),
        reasons=tuple(dict.fromkeys(reasons)),
    )
