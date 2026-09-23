from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from .paper_chain_refresh import default_rust_manifest_path


@dataclass(frozen=True)
class Phase9PositionDiscoveryItem:
    position_address: str
    pool_address: str
    owner: str
    lower_bin_id: int
    upper_bin_id: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PositionDiscoveryReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    source_scope: str
    pool_address: str
    positions_found: int
    positions_returned: int
    truncated: bool
    unique_owners: int
    positions: tuple[Phase9PositionDiscoveryItem, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def discover_pool_positions_with_rust(
    pool_address: str,
    *,
    limit: int = 250,
    rust_manifest_path: str | Path | None = None,
    rust_binary_path: str | Path | None = None,
    timeout_seconds: int = 120,
) -> Phase9PositionDiscoveryReport:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if limit < 1 or limit > 5_000:
        raise ValueError("limit must be between 1 and 5000")
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
            "discover-pool-positions-env",
            pool_address,
            str(limit),
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
            "discover-pool-positions-env",
            pool_address,
            str(limit),
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
            "Rust pool-position discovery failed for "
            f"{pool_address}: {detail[:2000]}"
        )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Rust pool-position discovery returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            "Rust pool-position discovery output must be a JSON object"
        )
    if str(payload.get("pool_address", "")).strip() != pool_address:
        raise ValueError(
            "Rust pool-position discovery returned a different pool_address"
        )
    raw_positions = payload.get("positions")
    if not isinstance(raw_positions, list):
        raise ValueError("Rust pool-position discovery positions must be a list")

    items: list[Phase9PositionDiscoveryItem] = []
    seen: set[str] = set()
    for raw in raw_positions:
        if not isinstance(raw, dict):
            raise ValueError("discovered position must be a JSON object")
        position = str(raw.get("position_address", "")).strip()
        owner = str(raw.get("owner", "")).strip()
        returned_pool = str(raw.get("pool_address", "")).strip()
        if not position or not owner:
            raise ValueError(
                "discovered position requires position_address and owner"
            )
        if returned_pool != pool_address:
            raise ValueError(
                "discovered position belongs to a different pool"
            )
        if position in seen:
            raise ValueError("Rust discovery returned duplicate positions")
        seen.add(position)
        items.append(
            Phase9PositionDiscoveryItem(
                position_address=position,
                pool_address=returned_pool,
                owner=owner,
                lower_bin_id=int(raw["lower_bin_id"]),
                upper_bin_id=int(raw["upper_bin_id"]),
            )
        )

    positions_found = int(payload.get("positions_found", len(items)))
    positions_returned = int(
        payload.get("positions_returned", len(items))
    )
    truncated = bool(payload.get("truncated", False))
    if positions_returned != len(items):
        raise ValueError(
            "Rust discovery positions_returned does not match payload"
        )
    if positions_found < positions_returned:
        raise ValueError(
            "Rust discovery positions_found cannot be below returned count"
        )
    if truncated != (positions_found > positions_returned):
        raise ValueError(
            "Rust discovery truncated flag does not match counts"
        )

    return Phase9PositionDiscoveryReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        source_scope="CURRENT_ONCHAIN_POSITION_COHORT",
        pool_address=pool_address,
        positions_found=positions_found,
        positions_returned=positions_returned,
        truncated=truncated,
        unique_owners=len({item.owner for item in items}),
        positions=tuple(items),
    )
