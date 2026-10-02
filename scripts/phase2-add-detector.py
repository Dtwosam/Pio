#!/opt/pio/python-learner/.venv/bin/python

from __future__ import annotations

import json
import os
import sqlite3
import subprocess


def run(args, *, input_text=None, timeout=None):
    return subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from meteora_learner.chain_ingest import ingest_chain_snapshot
from meteora_learner.settings import Settings
from meteora_learner.storage import Storage


ROOT = os.getenv("PIO_PHASE2_RUNTIME_ROOT", "/opt/pio")
RUST = os.getenv(
    "PIO_PHASE2_EXECUTOR",
    f"{ROOT}/rust-executor/target/release/meteora-executor",
)
PIO = os.getenv(
    "PIO_PHASE2_PIO_CLI",
    "/opt/pio/python-learner/.venv/bin/pio",
)
STATE_PATH = Path(
    os.getenv(
        "PIO_PHASE2_DETECTOR_STATE_PATH",
        "/opt/pio/data/phase2-add-detector-state.json",
    )
)

CACHE_DB = Path(
    os.getenv(
        "PIO_PHASE2_PRESTATE_CACHE_DB",
        "/opt/pio/data/phase2-prestate-cache.db",
    )
)

SETTINGS = Settings.from_env()
MAIN_DB = str(SETTINGS.database_path)
MAIN_STORAGE = Storage(SETTINGS.database_path)

RPC = os.environ["SOLANA_RPC_URL"]

# Operational polling cadence, not an evidence threshold.
DEFAULT_POOLS = {
    "54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ": 120.0,
    "DQ9weJhfiU4iL5LUoeshDrm5KxDHCMiSbnnKJz7buMcf": 300.0,
}


def parse_detector_pools(value=None):
    raw = value
    if raw is None:
        raw = os.getenv("PIO_PHASE2_DETECTOR_POOLS")
    if raw is None or not str(raw).strip():
        return dict(DEFAULT_POOLS)

    pools = {}
    for item in str(raw).split(","):
        entry = item.strip()
        address, separator, interval_raw = entry.partition(":")
        address = address.strip()
        interval_raw = interval_raw.strip()

        if not separator or not address or not interval_raw:
            raise ValueError(
                "PIO_PHASE2_DETECTOR_POOLS entries must be ADDRESS:SECONDS"
            )
        if address in pools:
            raise ValueError(
                f"duplicate Phase-2 detector pool: {address}"
            )

        interval = float(interval_raw)
        if not interval > 0:
            raise ValueError(
                "Phase-2 detector poll intervals must be positive"
            )
        pools[address] = interval

    if not pools:
        raise ValueError("at least one Phase-2 detector pool is required")
    return pools


POOLS = parse_detector_pools()


def rate_limit_cooldown_seconds(consecutive_rate_limits):
    if consecutive_rate_limits <= 0:
        return 0.0
    schedule = (
        60.0,
        120.0,
        300.0,
        600.0,
        900.0,
    )
    index = min(consecutive_rate_limits - 1, len(schedule) - 1)
    return schedule[index]


class RpcRateLimited(RuntimeError):
    pass


def is_rate_limited_text(value):
    text = str(value or "").casefold()
    return any(
        marker in text
        for marker in (
            "429 too many requests",
            "http 429",
            "http status 429",
            "status code: 429",
            "status code 429",
            "too many requests",
            "rate limit",
            "rate-limit",
            "ratelimit",
        )
    )


def error_category(exc):
    if isinstance(exc, RpcRateLimited):
        return "RPC_RATE_LIMITED"
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return "RPC_RATE_LIMITED"
    if is_rate_limited_text(exc):
        return "RPC_RATE_LIMITED"
    if isinstance(exc, subprocess.TimeoutExpired):
        return "EXECUTOR_TIMEOUT"
    return type(exc).__name__.upper()


def now():
    return datetime.now(timezone.utc).isoformat()


def log(kind, **fields):
    print(
        json.dumps(
            {"time": now(), "kind": kind, **fields},
            separators=(",", ":"),
        ),
        flush=True,
    )


def rpc_signatures(pool, *, until=None, limit=250):
    # Drain every signature page back to the saved cursor so a delayed
    # detector cannot silently skip a backlog larger than one page.
    page_size = max(1, min(int(limit), 1000))
    max_pages = 40

    all_rows = []
    seen = set()
    before = None

    retry_delays = (
        5.0,
        10.0,
        20.0,
        40.0,
    )

    for page_number in range(1, max_pages + 1):
        config = {
            "limit": page_size,
            "commitment": "confirmed",
        }

        if until:
            config["until"] = until

        if before:
            config["before"] = before

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                pool,
                config,
            ],
        }

        payload = json.dumps(body).encode()
        page = None

        for attempt in range(
            1,
            len(retry_delays) + 2,
        ):
            request = urllib.request.Request(
                RPC,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                },
            )

            try:
                with urllib.request.urlopen(
                    request,
                    timeout=30,
                ) as response:
                    reply = json.load(response)

            except urllib.error.HTTPError as exc:
                if exc.code != 429:
                    raise
                if attempt > len(retry_delays):
                    raise RpcRateLimited("RPC_RATE_LIMITED") from None

                delay = retry_delays[attempt - 1]

                retry_after = exc.headers.get("Retry-After")

                if retry_after:
                    try:
                        delay = max(
                            delay,
                            float(retry_after),
                        )
                    except ValueError:
                        pass

                log(
                    "RPC_BACKOFF",
                    operation="getSignaturesForAddress",
                    pool=pool,
                    page=page_number,
                    attempt=attempt,
                    sleep_seconds=delay,
                )

                time.sleep(delay)
                continue

            error = reply.get("error")

            if error:
                code = (
                    error.get("code")
                    if isinstance(error, dict)
                    else None
                )

                if code == 429:
                    if attempt <= len(retry_delays):
                        delay = retry_delays[attempt - 1]

                        log(
                            "RPC_BACKOFF",
                            operation="getSignaturesForAddress",
                            pool=pool,
                            page=page_number,
                            attempt=attempt,
                            sleep_seconds=delay,
                        )

                        time.sleep(delay)
                        continue
                    raise RpcRateLimited("RPC_RATE_LIMITED")

                category = (
                    f"RPC_ERROR_{code}"
                    if code is not None
                    else "RPC_RESPONSE_ERROR"
                )
                raise RuntimeError(category)

            page = reply.get("result") or []
            break

        if page is None:
            raise RuntimeError(
                "getSignaturesForAddress retries exhausted"
            )

        for row in page:
            sig = row.get("signature")

            if sig and sig not in seen:
                seen.add(sig)
                all_rows.append(row)

        if len(page) < page_size:
            return all_rows

        last_signature = page[-1].get("signature")

        if not last_signature:
            raise RuntimeError(
                "signature pagination missing last signature"
            )

        if last_signature == before:
            raise RuntimeError(
                "signature pagination stalled"
            )

        before = last_signature

        log(
            "SIGNATURE_PAGE",
            pool=pool,
            page=page_number,
            page_rows=len(page),
            accumulated_rows=len(all_rows),
        )

    raise RuntimeError(
        f"signature backlog exceeded {max_pages} pages; cursor retained"
    )

def inspect_transaction(signature):
    # Healthy useful work is not success-throttled. Provider rejection is
    # handled by explicit 429 backoff below and by the detector-wide cooldown.
    retry_delays = (
        2.0,
        4.0,
        8.0,
        16.0,
    )

    for attempt in range(
        1,
        len(retry_delays) + 2,
    ):
        proc = subprocess.run(
            [
                RUST,
                "inspect-transaction-events-env",
                signature,
            ],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )

        if proc.returncode == 0:
            return json.loads(proc.stdout), proc.stdout

        error_text = (
            (proc.stderr or "")
            + "\n"
            + (proc.stdout or "")
        )

        rate_limited = is_rate_limited_text(error_text)

        if rate_limited:
            if attempt <= len(retry_delays):
                delay = retry_delays[attempt - 1]

                log(
                    "RPC_BACKOFF",
                    operation="inspect_transaction",
                    signature=signature,
                    attempt=attempt,
                    sleep_seconds=delay,
                )

                time.sleep(delay)
                continue
            raise RpcRateLimited("RPC_RATE_LIMITED")

        raise RuntimeError(
            "inspect-transaction-events failed with status "
            f"{proc.returncode}"
        )

    raise RuntimeError(
        "inspect-transaction-events retries exhausted"
    )

def standalone_adds(tx, pool):
    requests = {
        int(item["instruction_index"]): item
        for item in tx.get("add_requests") or []
    }

    found = []

    for record in tx.get("events") or []:
        wrapper = record.get("event") or {}

        if wrapper.get("event_type") != "AddLiquidity":
            continue

        event = wrapper.get("event") or {}

        if event.get("lb_pair") != pool:
            continue

        parent = int(record["parent_ix_index"])

        # Fail closed. A rebalance-owned internal AddLiquidity does not have
        # a standalone add request at this same parent index.
        request = requests.get(parent)
        if request is None:
            continue

        found.append({
            "position": event.get("position"),
            "owner": event.get("from"),
            "parent_ix_index": parent,
            "active_bin_id": event.get("active_bin_id"),
            "request": request,
        })

    return found



def cache_candidate(
    pool,
    *,
    target_slot,
    active_bin_id,
):
    if not CACHE_DB.exists():
        return None

    conn = sqlite3.connect(CACHE_DB)
    conn.row_factory = sqlite3.Row

    try:
        row = conn.execute(
            """
            SELECT
                observed_at,
                pool_address,
                capture_slot_start,
                capture_slot_end,
                active_bin_id,
                bin_array_address,
                raw_json
            FROM prestate_snapshots
            WHERE pool_address = ?
              AND capture_slot_end < ?
              AND active_bin_id = ?
            ORDER BY capture_slot_end DESC, id DESC
            LIMIT 1
            """,
            (
                pool,
                int(target_slot),
                int(active_bin_id),
            ),
        ).fetchone()
    finally:
        conn.close()

    return dict(row) if row is not None else None


def main_has_snapshot(pool, observed_at):
    conn = sqlite3.connect(MAIN_DB)
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM chain_pool_snapshots
            WHERE pool_address = ?
              AND observed_at = ?
            LIMIT 1
            """,
            (pool, observed_at),
        ).fetchone()
    finally:
        conn.close()

    return row is not None


def promote_prestate(
    pool,
    *,
    target_slot,
    active_bin_id,
):
    candidate = cache_candidate(
        pool,
        target_slot=target_slot,
        active_bin_id=active_bin_id,
    )

    if candidate is None:
        log(
            "NO_CACHED_PRESTATE",
            pool=pool,
            target_slot=target_slot,
            active_bin_id=active_bin_id,
        )
        return None

    observed_at = str(candidate["observed_at"])

    if not main_has_snapshot(pool, observed_at):
        snapshot = json.loads(candidate["raw_json"])

        ingest_chain_snapshot(
            MAIN_STORAGE,
            snapshot,
            observed_at=observed_at,
        )

    log(
        "PRESTATE_PROMOTED",
        pool=pool,
        observed_at=observed_at,
        capture_slot_start=candidate["capture_slot_start"],
        capture_slot_end=candidate["capture_slot_end"],
        target_slot=target_slot,
        active_bin_id=active_bin_id,
        bin_array_address=candidate["bin_array_address"],
    )

    return candidate


def ingest_transaction(raw):
    proc = run(
        [PIO, "ingest-transaction-events"],
        input_text=raw,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"transaction ingest failed: {proc.stderr.strip()[:1000]}"
        )


def collect_history(position):
    proc = run(
        [
            PIO,
            "collect-position-history",
            "--position",
            position,
        ],
        timeout=90,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"collect-position-history failed: "
            f"{proc.stderr.strip()[:1000]}"
        )

    return proc.stdout


def composition_prestate(position):
    proc = run(
        [
            PIO,
            "composition-prestate",
            "--position",
            position,
        ]
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"composition-prestate failed: "
            f"{proc.stderr.strip()[:1000]}"
        )

    return json.loads(proc.stdout)


def verify_prestate(candidate):
    args = [
        RUST,
        "verify-prestate-env",
        candidate["signature"],
        str(candidate["capture_slot_start"]),
        str(candidate["capture_slot_end"]),
        *candidate["verification_addresses"],
    ]

    proc = run(args, timeout=120)

    # The verifier may return nonzero for an ineligible proof while still
    # returning a useful JSON proof. Preserve that proof if present.
    if not proc.stdout.strip():
        error_text = (proc.stderr or "") + "\n" + (proc.stdout or "")
        if is_rate_limited_text(error_text):
            raise RpcRateLimited("RPC_RATE_LIMITED")
        raise RuntimeError(
            "verify-prestate produced no proof"
        )

    proof = json.loads(proc.stdout)
    clear_rate_limit_cooldown()

    ingest = run(
        [
            PIO,
            "ingest-prestate-verification",
            "--snapshot-observed-at",
            candidate["snapshot_observed_at"],
            "--pool",
            candidate["pool_address"],
        ],
        input_text=proc.stdout,
    )

    if ingest.returncode != 0:
        raise RuntimeError(
            f"prestate proof ingest failed: "
            f"{ingest.stderr.strip()[:1000]}"
        )

    return proof


def reconcile(position):
    proc = run(
        [
            PIO,
            "reconcile-composition",
            "--position",
            position,
        ]
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"reconcile-composition failed: "
            f"{proc.stderr.strip()[:1000]}"
        )

    return json.loads(proc.stdout)


def phase2_evidence():
    proc = run([PIO, "phase2-evidence"])
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except Exception:
        return None


def load_state():
    if not STATE_PATH.exists():
        return {
            "cursors": {},
            "processed": [],
            "pending": {},
        }

    with STATE_PATH.open() as f:
        return json.load(f)


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)


def advance_cursor(state, pool, signature):
    state["cursors"][pool] = signature
    save_state(state)


def pending_record(pool, signature, position, reason, add):
    record = {
        "pool": pool,
        "signature": signature,
        "position": position,
        "reason": reason,
        "last_attempt": now(),
    }
    if add.get("target_slot") is not None:
        record["target_slot"] = int(add["target_slot"])
    if add.get("active_bin_id") is not None:
        record["active_bin_id"] = int(add["active_bin_id"])
    return record


def process_candidate(state, pool, signature, add):
    position = str(add["position"])
    key = f"{signature}:{position}"

    try:
        target_slot = add.get("target_slot")
        active_bin_id = add.get("active_bin_id")
        if target_slot is not None and active_bin_id is not None:
            promoted = promote_prestate(
                pool,
                target_slot=int(target_slot),
                active_bin_id=int(active_bin_id),
            )
            if promoted is None:
                state["pending"][key] = pending_record(
                    pool,
                    signature,
                    position,
                    "NO_CACHED_PRESTATE",
                    add,
                )
                log(
                    "PENDING_PRESTATE",
                    pool=pool,
                    signature=signature,
                    position=position,
                    target_slot=int(target_slot),
                    active_bin_id=int(active_bin_id),
                )
                return

        collect_history(position)

        report = composition_prestate(position)

        candidate = next(
            (
                item
                for item in report.get("candidates", [])
                if str(item.get("signature")) == signature
            ),
            None,
        )

        if candidate is None:
            state["pending"][key] = pending_record(
                pool,
                signature,
                position,
                "Meteora history has not exposed target add yet",
                add,
            )
            log(
                "PENDING_HISTORY",
                pool=pool,
                signature=signature,
                position=position,
            )
            return

        if not candidate.get("eligible_for_verification"):
            reason = candidate.get("ineligibility_reason")

            state["processed"].append(key)
            state["pending"].pop(key, None)

            log(
                "PRESTATE_NOT_ELIGIBLE",
                pool=pool,
                signature=signature,
                position=position,
                reason=reason,
            )
            return

        proof = verify_prestate(candidate)

        log(
            "PRESTATE_VERIFIED",
            pool=pool,
            signature=signature,
            position=position,
            eligible=proof.get("eligible"),
            reasons=proof.get("reasons"),
        )

        result = reconcile(position)

        state["processed"].append(key)
        state["pending"].pop(key, None)

        log(
            "RECONCILIATION",
            pool=pool,
            signature=signature,
            position=position,
            eligible_samples=result.get("eligible_samples"),
            exact_samples=result.get("exact_samples"),
            mismatched_samples=result.get("mismatched_samples"),
        )

        evidence = phase2_evidence()
        if evidence is not None:
            log(
                "PHASE2",
                composition_eligible_samples=(
                    evidence.get("composition_eligible_samples")
                ),
                composition_exact_samples=(
                    evidence.get("composition_exact_samples")
                ),
                composition_mismatched_samples=(
                    evidence.get("composition_mismatched_samples")
                ),
                evidence_gaps=evidence.get("evidence_gaps"),
            )

    except RpcRateLimited:
        state["pending"][key] = pending_record(
            pool,
            signature,
            position,
            "RPC_RATE_LIMITED",
            add,
        )

        log(
            "CANDIDATE_ERROR",
            pool=pool,
            signature=signature,
            position=position,
            category="RPC_RATE_LIMITED",
        )
        raise

    except Exception as exc:
        category = error_category(exc)
        state["pending"][key] = pending_record(
            pool,
            signature,
            position,
            category,
            add,
        )

        log(
            "CANDIDATE_ERROR",
            pool=pool,
            signature=signature,
            position=position,
            category=category,
        )


rate_limit_streak = 0
rpc_cooldown_until = 0.0


def enter_rate_limit_cooldown(*, operation, pool=None):
    global rate_limit_streak, rpc_cooldown_until

    rate_limit_streak += 1
    delay = rate_limit_cooldown_seconds(rate_limit_streak)
    rpc_cooldown_until = time.monotonic() + delay

    fields = {
        "operation": operation,
        "consecutive_rate_limits": rate_limit_streak,
        "sleep_seconds": delay,
    }
    if pool is not None:
        fields["pool"] = pool
    log("RPC_COOLDOWN", **fields)
    return delay


def clear_rate_limit_cooldown():
    global rate_limit_streak, rpc_cooldown_until
    rate_limit_streak = 0
    rpc_cooldown_until = 0.0


state = load_state()

for pool in POOLS:
    state["cursors"].setdefault(pool, None)

save_state(state)

log("START", pools=list(POOLS))

# Initialize only pools without a persisted cursor. We intentionally do not
# retroactively claim prestate coverage before this detector existed.
for pool in POOLS:
    if state["cursors"].get(pool):
        continue

    try:
        rows = rpc_signatures(pool, limit=1)
        state["cursors"][pool] = (
            rows[0]["signature"] if rows else None
        )
        save_state(state)

        log(
            "BASELINE",
            pool=pool,
            cursor=state["cursors"][pool],
        )
    except RpcRateLimited:
        enter_rate_limit_cooldown(
            operation="baseline",
            pool=pool,
        )
        log(
            "ERROR",
            pool=pool,
            operation="baseline",
            category="RPC_RATE_LIMITED",
        )
        break
    except Exception as exc:
        log(
            "ERROR",
            pool=pool,
            operation="baseline",
            category=error_category(exc),
        )
    else:
        clear_rate_limit_cooldown()


next_poll = {pool: 0.0 for pool in POOLS}
next_pending_retry = 0.0

while True:
    mono = time.monotonic()

    if mono < rpc_cooldown_until:
        remaining = rpc_cooldown_until - mono
        time.sleep(min(1.0, max(0.25, remaining)))
        continue

    # Retry candidates when the Meteora Data API was behind the chain.
    if mono >= next_pending_retry:
        next_pending_retry = mono + 30.0

        for key, item in list(state["pending"].items()):
            if key in state["processed"]:
                state["pending"].pop(key, None)
                continue

            try:
                process_candidate(
                    state,
                    item["pool"],
                    item["signature"],
                    {
                        "position": item["position"],
                        "target_slot": item.get("target_slot"),
                        "active_bin_id": item.get("active_bin_id"),
                    },
                )
            except RpcRateLimited:
                save_state(state)
                enter_rate_limit_cooldown(
                    operation="pending_candidate",
                    pool=item["pool"],
                )
                log(
                    "PENDING_RETRY_PAUSED",
                    category="RPC_RATE_LIMITED",
                )
                break
            save_state(state)

    if time.monotonic() < rpc_cooldown_until:
        continue

    for pool, interval in POOLS.items():
        if mono < next_poll[pool]:
            continue

        next_poll[pool] = mono + interval

        cursor = state["cursors"].get(pool)

        if not cursor:
            try:
                rows = rpc_signatures(pool, limit=1)
            except RpcRateLimited:
                enter_rate_limit_cooldown(
                    operation="baseline_recovery",
                    pool=pool,
                )
                log(
                    "ERROR",
                    pool=pool,
                    operation="baseline_recovery",
                    category="RPC_RATE_LIMITED",
                )
                break
            except Exception as exc:
                log(
                    "ERROR",
                    pool=pool,
                    operation="baseline_recovery",
                    category=error_category(exc),
                )
                continue

            clear_rate_limit_cooldown()
            state["cursors"][pool] = (
                rows[0]["signature"] if rows else None
            )
            save_state(state)
            log(
                "BASELINE_RECOVERED",
                pool=pool,
                cursor=state["cursors"][pool],
            )
            continue

        try:
            rows = rpc_signatures(
                pool,
                until=cursor,
                limit=1000,
            )

            clear_rate_limit_cooldown()

            if not rows:
                continue

            log(
                "ACTIVITY",
                pool=pool,
                new_signatures=len(rows),
                previous_cursor=cursor,
                newest_signature=rows[0]["signature"],
            )

            batch_failed = False
            rate_limited_batch = False

            # Oldest -> newest.
            for row in reversed(rows):
                signature = row["signature"]

                if row.get("err") is not None:
                    advance_cursor(
                        state,
                        pool,
                        signature,
                    )
                    continue

                try:
                    tx, raw = inspect_transaction(signature)
                    clear_rate_limit_cooldown()
                except RpcRateLimited:
                    batch_failed = True
                    rate_limited_batch = True
                    log(
                        "INSPECT_ERROR",
                        pool=pool,
                        signature=signature,
                        category="RPC_RATE_LIMITED",
                    )
                    break
                except Exception as exc:
                    batch_failed = True
                    log(
                        "INSPECT_ERROR",
                        pool=pool,
                        signature=signature,
                        category=error_category(exc),
                    )
                    break

                adds = standalone_adds(tx, pool)

                if not adds:
                    continue

                ingest_transaction(raw)

                for add in adds:
                    key = f"{signature}:{add['position']}"

                    if key in state["processed"]:
                        continue

                    target_slot = int(tx["slot"])
                    active_bin_id = int(add["active_bin_id"])
                    candidate_add = dict(add)
                    candidate_add["target_slot"] = target_slot
                    candidate_add["active_bin_id"] = active_bin_id

                    log(
                        "STANDALONE_ADD",
                        pool=pool,
                        signature=signature,
                        slot=tx.get("slot"),
                        position=add["position"],
                        parent_ix_index=add["parent_ix_index"],
                        active_bin_id=add["active_bin_id"],
                        instruction_type=(
                            add["request"].get("instruction_type")
                        ),
                    )

                    process_candidate(
                        state,
                        pool,
                        signature,
                        candidate_add,
                    )

                advance_cursor(
                    state,
                    pool,
                    signature,
                )

            if batch_failed:
                log(
                    "BATCH_RETRY",
                    pool=pool,
                    retained_cursor=cursor,
                    attempted_signatures=len(rows),
                    rate_limited=rate_limited_batch,
                )
                if rate_limited_batch:
                    enter_rate_limit_cooldown(
                        operation="inspect_transaction",
                        pool=pool,
                    )
                    break

        except RpcRateLimited:
            enter_rate_limit_cooldown(
                operation="scan",
                pool=pool,
            )
            log(
                "ERROR",
                pool=pool,
                operation="scan",
                category="RPC_RATE_LIMITED",
            )
            break
        except Exception as exc:
            log(
                "ERROR",
                pool=pool,
                operation="scan",
                category=error_category(exc),
            )

    time.sleep(0.25)
