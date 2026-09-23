from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable

from .chain_ingest import ingest_chain_snapshot
from .paper_chain_collection import (
    PaperChainCollectionQueue,
    build_paper_chain_collection_queue,
)
from .storage import Storage


InspectPool = Callable[[str, int], dict[str, Any]]


@dataclass(frozen=True)
class PaperChainRefreshItem:
    pool_address: str
    status: str
    bin_arrays: int | None
    bins: int | None
    error: str | None


@dataclass(frozen=True)
class PaperChainRefreshReport:
    account_id: str | None
    queue: PaperChainCollectionQueue
    pools_attempted: int
    pools_refreshed: int
    pools_failed: int
    items: tuple[PaperChainRefreshItem, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def default_rust_manifest_path() -> Path:
    return Path(__file__).resolve().parents[3] / "rust-executor" / "Cargo.toml"


def inspect_pool_with_rust(
    pool_address: str,
    array_radius: int,
    *,
    rust_manifest_path: str | Path | None = None,
    rust_binary_path: str | Path | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if array_radius < 0:
        raise ValueError("array_radius cannot be negative")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    rpc_url = os.getenv("SOLANA_RPC_URL") or os.getenv("RPC_URL")
    if not rpc_url:
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
            "inspect-pool-env",
            pool_address,
            str(array_radius),
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
            "inspect-pool-env",
            pool_address,
            str(array_radius),
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
            f"Rust inspect-pool failed for {pool_address}: {detail[:2000]}"
        )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Rust inspect-pool returned invalid JSON for {pool_address}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Rust inspect-pool output must be a JSON object")
    return payload


def refresh_paper_chain_state(
    storage: Storage,
    *,
    account_id: str | None = None,
    max_age_seconds: int = 300,
    array_radius: int = 1,
    as_of: str | None = None,
    inspector: InspectPool | None = None,
    rust_manifest_path: str | Path | None = None,
    rust_binary_path: str | Path | None = None,
    timeout_seconds: int = 120,
    ingest_observed_at: str | None = None,
) -> PaperChainRefreshReport:
    """
    Refresh missing/stale open-paper pool state through the read-only Rust inspector.

    No wallet is used. Failures are isolated per pool and never converted into
    synthetic chain data.
    """
    queue = build_paper_chain_collection_queue(
        storage,
        account_id=account_id,
        max_age_seconds=max_age_seconds,
        array_radius=array_radius,
        as_of=as_of,
    )

    if inspector is None:
        def inspect(pool: str, radius: int) -> dict[str, Any]:
            return inspect_pool_with_rust(
                pool,
                radius,
                rust_manifest_path=rust_manifest_path,
                rust_binary_path=rust_binary_path,
                timeout_seconds=timeout_seconds,
            )
    else:
        inspect = inspector

    items: list[PaperChainRefreshItem] = []
    attempted = 0
    refreshed = 0
    failed = 0

    for work in queue.items:
        if not work.needs_collection:
            items.append(
                PaperChainRefreshItem(
                    pool_address=work.pool_address,
                    status="FRESH",
                    bin_arrays=None,
                    bins=None,
                    error=None,
                )
            )
            continue

        attempted += 1
        try:
            payload = inspect(work.pool_address, array_radius)
            if str(payload.get("pool_address", "")) != work.pool_address:
                raise ValueError(
                    "Rust inspector returned a different pool_address"
                )
            result = ingest_chain_snapshot(
                storage,
                payload,
                observed_at=ingest_observed_at,
            )
            refreshed += 1
            items.append(
                PaperChainRefreshItem(
                    pool_address=work.pool_address,
                    status="REFRESHED",
                    bin_arrays=result.bin_arrays,
                    bins=result.bins,
                    error=None,
                )
            )
        except Exception as exc:
            failed += 1
            items.append(
                PaperChainRefreshItem(
                    pool_address=work.pool_address,
                    status="FAILED",
                    bin_arrays=None,
                    bins=None,
                    error=str(exc)[:2000],
                )
            )

    return PaperChainRefreshReport(
        account_id=account_id,
        queue=queue,
        pools_attempted=attempted,
        pools_refreshed=refreshed,
        pools_failed=failed,
        items=tuple(items),
    )
