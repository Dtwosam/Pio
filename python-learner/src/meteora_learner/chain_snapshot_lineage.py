from __future__ import annotations

import hashlib
import json
from typing import Any


def chain_snapshot_source_record(row: Any) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "pool_address": str(row[1]),
        "observed_at": str(row[2]),
        "active_bin_id": int(row[3]),
    }


def chain_snapshot_source_sha256(
    records: list[dict[str, Any]],
) -> str:
    canonical = json.dumps(
        records,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
