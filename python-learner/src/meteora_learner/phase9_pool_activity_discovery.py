from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from .paper_chain_refresh import default_rust_manifest_path


@dataclass(frozen=True)
class Phase9HistoricalPoolPosition:
    position_address: str
    owner: str
    latest_matching_signature: str
    latest_matching_slot: int
    latest_matching_block_time: int | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PoolActivityDiscoveryReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    source_scope: str
    pool_address: str
    before_signature: str | None
    until_signature: str | None
    newest_signature: str | None
    signatures_requested: int
    signatures_scanned: int
    failed_transactions: int
    matching_transactions: int
    positions_found: int
    has_more: bool
    next_before_signature: str | None
    positions: tuple[Phase9HistoricalPoolPosition, ...]
    errors: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def discover_historical_pool_activity_with_rust(
    pool_address: str,
    *,
    limit: int = 100,
    before_signature: str | None = None,
    until_signature: str | None = None,
    rust_manifest_path: str | Path | None = None,
    rust_binary_path: str | Path | None = None,
    timeout_seconds: int = 300,
) -> Phase9PoolActivityDiscoveryReport:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if limit < 1 or limit > 1_000:
        raise ValueError("limit must be between 1 and 1000")
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
    args = [
        "discover-pool-activity-env",
        pool_address,
        str(limit),
    ]
    if before_signature:
        args.append(before_signature)
    elif until_signature:
        args.append("")
    if until_signature:
        args.append(until_signature)

    if configured_binary:
        binary = Path(configured_binary)
        if not binary.exists():
            raise ValueError(f"Rust executor binary not found: {binary}")
        command = [str(binary), *args]
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
            *args,
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
            "Rust historical pool-activity discovery failed for "
            f"{pool_address}: {detail[:2000]}"
        )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Rust historical pool-activity discovery returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            "Rust historical pool-activity output must be a JSON object"
        )
    if payload.get("research_only") is not True:
        raise ValueError(
            "historical pool-activity discovery must remain research-only"
        )
    if payload.get("read_only_capture") is not True:
        raise ValueError(
            "historical pool-activity discovery must remain read-only"
        )
    if str(payload.get("pool_address", "")).strip() != pool_address:
        raise ValueError(
            "historical pool-activity discovery returned a different pool"
        )

    raw_before = payload.get("before_signature")
    returned_before = (
        str(raw_before).strip()
        if raw_before is not None
        else None
    )
    expected_before = before_signature.strip() if before_signature else None
    if returned_before != expected_before:
        raise ValueError(
            "historical pool-activity discovery cursor mismatch"
        )
    raw_until = payload.get("until_signature")
    returned_until = (
        str(raw_until).strip()
        if raw_until is not None
        else None
    )
    expected_until = until_signature.strip() if until_signature else None
    if returned_until != expected_until:
        raise ValueError(
            "historical pool-activity discovery until cursor mismatch"
        )
    raw_newest = payload.get("newest_signature")
    newest_signature = (
        str(raw_newest).strip()
        if raw_newest is not None
        else None
    )

    signatures_requested = int(payload.get("signatures_requested", -1))
    signatures_scanned = int(payload.get("signatures_scanned", -1))
    failed_transactions = int(payload.get("failed_transactions", -1))
    matching_transactions = int(payload.get("matching_transactions", -1))
    positions_found = int(payload.get("positions_found", -1))
    has_more = bool(payload.get("has_more", False))
    if signatures_requested != limit:
        raise ValueError(
            "historical pool-activity signatures_requested mismatch"
        )
    if not 0 <= signatures_scanned <= limit:
        raise ValueError(
            "historical pool-activity signatures_scanned is invalid"
        )
    if not 0 <= failed_transactions <= signatures_scanned:
        raise ValueError(
            "historical pool-activity failed_transactions is invalid"
        )
    if not 0 <= matching_transactions <= signatures_scanned:
        raise ValueError(
            "historical pool-activity matching_transactions is invalid"
        )
    if has_more != (signatures_scanned == limit):
        raise ValueError(
            "historical pool-activity has_more does not match page size"
        )

    raw_next = payload.get("next_before_signature")
    next_before = (
        str(raw_next).strip()
        if raw_next is not None
        else None
    )
    if signatures_scanned > 0 and not next_before:
        raise ValueError(
            "historical pool-activity page is missing its next cursor"
        )
    if signatures_scanned > 0 and not newest_signature:
        raise ValueError(
            "historical pool-activity page is missing its newest signature"
        )
    if signatures_scanned == 0 and (
        next_before is not None or newest_signature is not None
    ):
        raise ValueError(
            "empty historical pool-activity page cannot have signature cursors"
        )

    raw_positions = payload.get("positions")
    if not isinstance(raw_positions, list):
        raise ValueError(
            "historical pool-activity positions must be a list"
        )
    positions: list[Phase9HistoricalPoolPosition] = []
    seen: set[str] = set()
    for raw in raw_positions:
        if not isinstance(raw, dict):
            raise ValueError(
                "historical pool-activity position must be an object"
            )
        position = str(raw.get("position_address", "")).strip()
        owner = str(raw.get("owner", "")).strip()
        signature = str(
            raw.get("latest_matching_signature", "")
        ).strip()
        slot = int(raw.get("latest_matching_slot", -1))
        block_raw = raw.get("latest_matching_block_time")
        block_time = int(block_raw) if block_raw is not None else None
        if not position or not owner or not signature or slot < 0:
            raise ValueError(
                "historical pool-activity position is incomplete"
            )
        if position in seen:
            raise ValueError(
                "historical pool-activity returned duplicate positions"
            )
        seen.add(position)
        positions.append(
            Phase9HistoricalPoolPosition(
                position_address=position,
                owner=owner,
                latest_matching_signature=signature,
                latest_matching_slot=slot,
                latest_matching_block_time=block_time,
            )
        )

    if positions_found != len(positions):
        raise ValueError(
            "historical pool-activity positions_found mismatch"
        )
    raw_errors = payload.get("errors", [])
    if not isinstance(raw_errors, list):
        raise ValueError(
            "historical pool-activity errors must be a list"
        )

    return Phase9PoolActivityDiscoveryReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        source_scope="HISTORICAL_POOL_SIGNATURE_ACTIVITY",
        pool_address=pool_address,
        before_signature=returned_before,
        until_signature=returned_until,
        newest_signature=newest_signature,
        signatures_requested=signatures_requested,
        signatures_scanned=signatures_scanned,
        failed_transactions=failed_transactions,
        matching_transactions=matching_transactions,
        positions_found=positions_found,
        has_more=has_more,
        next_before_signature=next_before,
        positions=tuple(positions),
        errors=tuple(str(value)[:2000] for value in raw_errors),
    )
