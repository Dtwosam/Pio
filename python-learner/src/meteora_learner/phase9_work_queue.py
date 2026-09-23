from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import shlex
from typing import Any

from .phase9_validation import (
    Phase9ResearchBundleCriteria,
    evaluate_phase9_promotion,
    evaluate_phase9_research_bundle,
)
from .phase_promotion import PHASE8, PHASE8_EVIDENCE_TYPE
from .storage import Storage


DEFAULT_PUBKEY = "11111111111111111111111111111111"


@dataclass(frozen=True)
class Phase9WorkItem:
    task_type: str
    scope: str
    reason: str
    shell_command: str | None


@dataclass(frozen=True)
class Phase9WorkQueue:
    phase8_promoted: bool
    research_bundle_ready: bool
    promotion_ready: bool
    candidate_pools: tuple[str, ...]
    items: tuple[Phase9WorkItem, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _q(value: object) -> str:
    return shlex.quote(str(value))


def _candidate_pools(
    storage: Storage,
    *,
    limit: int = 8,
) -> tuple[str, ...]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT pool_address, COUNT(*) AS observations
            FROM chain_pool_snapshots
            GROUP BY pool_address
            ORDER BY observations DESC, pool_address ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _latest_pool_mints(
    storage: Storage,
    *,
    pool_address: str,
) -> tuple[str, ...]:
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
    values: list[str] = []
    for raw in row:
        if raw is None:
            continue
        mint = str(raw).strip()
        if not mint or mint == DEFAULT_PUBKEY:
            continue
        if mint not in values:
            values.append(mint)
    return tuple(values)


def _mint_snapshot_exists(
    storage: Storage,
    *,
    mint_address: str,
) -> bool:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM token_mint_snapshots
            WHERE mint_address = ?
            LIMIT 1
            """,
            (mint_address,),
        ).fetchone()
    return row is not None


def _latest_retraining_dataset_cycle(
    storage: Storage,
) -> str | None:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT evidence_json
            FROM model_live_evidence
            WHERE evidence_type = 'CONTINUOUS_RETRAIN_DATASET_V1'
              AND status = 'BUILT'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(str(row[0]))
    cycle_id = str(payload.get("cycle_id", "")).strip()
    target_version = str(
        payload.get("target_dataset_version", "")
    ).strip()
    output_file = str(payload.get("output_file", "")).strip()
    dataset = payload.get("dataset")
    if (
        not cycle_id
        or not target_version
        or not output_file
        or not isinstance(dataset, dict)
        or not str(dataset.get("dataset_sha256", "")).strip()
        or str(dataset.get("dataset_version", "")).strip()
        != target_version
    ):
        return None
    return cycle_id


def build_phase9_work_queue(
    storage: Storage,
    *,
    criteria: Phase9ResearchBundleCriteria = (
        Phase9ResearchBundleCriteria()
    ),
    rpc_url: str | None = None,
) -> Phase9WorkQueue:
    phase8_promoted = storage.phase_is_promoted(
        PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
    )
    bundle = evaluate_phase9_research_bundle(
        storage,
        criteria=criteria,
    )
    promotion = evaluate_phase9_promotion(
        storage,
        criteria=criteria,
    )
    pools = _candidate_pools(storage)
    items: list[Phase9WorkItem] = []

    if not phase8_promoted:
        items.append(
            Phase9WorkItem(
                task_type="PHASE8_PROMOTION_REQUIRED",
                scope="PHASE8",
                reason=(
                    "Phase 8 must be persistently promoted before "
                    "Phase 9 research can qualify"
                ),
                shell_command="pio phase8-validate --require-ready --persist-ready",
            )
        )

    if bundle.adaptive_multi_pool.qualified_records < 1:
        command = None
        if len(pools) >= 3:
            command = (
                "pio phase9-research-validate --pools "
                + _q(",".join(pools[:3]))
                + " --persist --require-qualified"
            )
        items.append(
            Phase9WorkItem(
                task_type="ADAPTIVE_MULTI_POOL",
                scope="__MULTI_POOL__",
                reason=(
                    "qualified adaptive/regime multi-pool evidence is missing"
                    if command is not None
                    else "at least three persisted chain-history pools are needed"
                ),
                shell_command=command,
            )
        )

    qualified_mint = set(bundle.mint_risk.qualified_pools)
    mint_needed = max(
        0,
        criteria.min_mint_risk_pools
        - bundle.mint_risk.qualified_records,
    )
    mint_candidate_pools = [
        value for value in pools if value not in qualified_mint
    ][:mint_needed]
    for pool in mint_candidate_pools:
        required_mints = _latest_pool_mints(
            storage,
            pool_address=pool,
        )
        missing_mints = [
            mint
            for mint in required_mints
            if not _mint_snapshot_exists(
                storage,
                mint_address=mint,
            )
        ]
        if missing_mints:
            rpc = rpc_url if rpc_url is not None else "<RPC_URL>"
            for mint in missing_mints:
                filename = f"mint-{mint}.json"
                items.append(
                    Phase9WorkItem(
                        task_type="MINT_SNAPSHOT",
                        scope=mint,
                        reason=(
                            f"pool {pool} requires authoritative mint state "
                            "before mint-risk research can run"
                        ),
                        shell_command=(
                            "meteora-executor inspect-mint "
                            + _q(rpc)
                            + " "
                            + _q(mint)
                            + " > "
                            + _q(filename)
                            + " && pio mint-snapshot-ingest --file "
                            + _q(filename)
                        ),
                    )
                )
            continue

        if required_mints:
            items.append(
                Phase9WorkItem(
                    task_type="MINT_RISK",
                    scope=pool,
                    reason="qualified persisted mint-risk evidence is needed",
                    shell_command=(
                        "pio mint-risk-research --pool "
                        + _q(pool)
                        + " --persist --require-qualified"
                    ),
                )
            )
        else:
            items.append(
                Phase9WorkItem(
                    task_type="MINT_RISK",
                    scope=pool,
                    reason=(
                        "latest chain pool snapshot does not expose token "
                        "mints needed for mint-risk research"
                    ),
                    shell_command=None,
                )
            )
    if mint_needed > len(mint_candidate_pools):
        items.append(
            Phase9WorkItem(
                task_type="MINT_RISK",
                scope="POOL_REQUIRED",
                reason=(
                    f"{mint_needed - len(mint_candidate_pools)} additional "
                    "chain-observed pool(s) are required for mint-risk evidence"
                ),
                shell_command=None,
            )
        )

    qualified_wallet = set(bundle.wallet_flow.qualified_pools)
    wallet_needed = max(
        0,
        criteria.min_wallet_flow_pools
        - bundle.wallet_flow.qualified_records,
    )
    for pool in [
        value for value in pools if value not in qualified_wallet
    ][:wallet_needed]:
        items.append(
            Phase9WorkItem(
                task_type="WALLET_FLOW",
                scope=pool,
                reason="qualified persisted wallet-flow evidence is needed",
                shell_command=(
                    "pio wallet-flow-research --pool "
                    + _q(pool)
                    + " --persist --require-qualified"
                ),
            )
        )
    if wallet_needed > 0 and not any(
        item.task_type == "WALLET_FLOW" for item in items
    ):
        items.append(
            Phase9WorkItem(
                task_type="WALLET_FLOW",
                scope="POOL_REQUIRED",
                reason=(
                    f"{wallet_needed} additional qualified wallet-flow "
                    "pool(s) are required"
                ),
                shell_command=None,
            )
        )

    adaptive_lineage_invalid = any(
        "immutable chain snapshot IDs with matching source hashes"
        in reason
        for reason in bundle.reasons
    )
    if adaptive_lineage_invalid:
        if pools:
            joined = ",".join(pools)
            items.append(
                Phase9WorkItem(
                    task_type="ADAPTIVE_MULTI_POOL_REPAIR",
                    scope="__MULTI_POOL__",
                    reason=(
                        "qualified adaptive/regime evidence does not "
                        "reproduce from its persisted chain snapshots"
                    ),
                    shell_command=(
                        "pio phase9-research-validate --pools "
                        + _q(joined)
                        + " --persist"
                    ),
                )
            )
        else:
            items.append(
                Phase9WorkItem(
                    task_type="ADAPTIVE_MULTI_POOL_REPAIR",
                    scope="POOL_REQUIRED",
                    reason=(
                        "adaptive/regime lineage is invalid and no "
                        "chain-observed pool corpus is available"
                    ),
                    shell_command=None,
                )
            )

    mint_lineage_invalid = any(
        "authoritative pool and mint snapshot IDs" in reason
        for reason in bundle.reasons
    )
    if mint_lineage_invalid:
        for pool in bundle.mint_risk.qualified_pools:
            required_mints = _latest_pool_mints(
                storage,
                pool_address=pool,
            )
            missing_mints = [
                mint
                for mint in required_mints
                if not _mint_snapshot_exists(
                    storage,
                    mint_address=mint,
                )
            ]
            if missing_mints:
                rpc = rpc_url if rpc_url is not None else "<RPC_URL>"
                for mint in missing_mints:
                    filename = f"mint-{mint}.json"
                    items.append(
                        Phase9WorkItem(
                            task_type="MINT_SNAPSHOT_REPAIR",
                            scope=mint,
                            reason=(
                                f"qualified mint-risk evidence for {pool} "
                                "cannot resolve authoritative snapshot lineage"
                            ),
                            shell_command=(
                                "meteora-executor inspect-mint "
                                + _q(rpc)
                                + " "
                                + _q(mint)
                                + " > "
                                + _q(filename)
                                + " && pio mint-snapshot-ingest --file "
                                + _q(filename)
                            ),
                        )
                    )
            else:
                items.append(
                    Phase9WorkItem(
                        task_type="MINT_RISK_REPAIR",
                        scope=pool,
                        reason=(
                            "qualified mint-risk evidence is not bound to "
                            "valid authoritative snapshot IDs"
                        ),
                        shell_command=(
                            "pio mint-risk-research --pool "
                            + _q(pool)
                            + " --persist --require-qualified"
                        ),
                    )
                )

    wallet_lineage_invalid = any(
        "immutable source event IDs and SHA-256" in reason
        for reason in bundle.reasons
    )
    if wallet_lineage_invalid:
        for pool in bundle.wallet_flow.qualified_pools:
            items.append(
                Phase9WorkItem(
                    task_type="WALLET_FLOW_REPAIR",
                    scope=pool,
                    reason=(
                        "qualified wallet-flow evidence does not reproduce "
                        "from its persisted source-event window"
                    ),
                    shell_command=(
                        "pio wallet-flow-research --pool "
                        + _q(pool)
                        + " --persist --require-qualified"
                    ),
                )
            )

    if (
        bundle.static_hedge.qualified_records
        < criteria.min_static_hedge_pools
    ):
        items.append(
            Phase9WorkItem(
                task_type="STATIC_HEDGE",
                scope="EXPLICIT_INSTRUMENT_REQUIRED",
                reason=(
                    "hedge research requires explicit LP token amounts plus "
                    "instrument, venue, liquidity, leverage, funding and "
                    "trading-cost assumptions"
                ),
                shell_command=None,
            )
        )

    if (
        criteria.require_portfolio_allocation
        and bundle.portfolio_allocation.qualified_records < 1
    ):
        items.append(
            Phase9WorkItem(
                task_type="PORTFOLIO_ALLOCATION",
                scope="REPRODUCIBLE_CANDIDATE_PIPELINE",
                reason=(
                    "portfolio allocation requires a fixed multi-pool "
                    "candidate corpus plus explicit account-state and quote-"
                    "budget assumptions"
                ),
                shell_command=(
                    "pio multi-pool-research "
                    "--file <POOL_INPUTS_JSON> "
                    "--equity <EQUITY> --cash <CASH> "
                    "--deployed <DEPLOYED> --drawdown-bps <BPS> "
                    "--persist-phase9-candidates "
                    "> phase9-multi-pool.json && "
                    "pio portfolio-allocation-research "
                    "--file phase9-multi-pool.json "
                    "--budget-quote <QUOTE> "
                    "--persist --require-qualified"
                ),
            )
        )

    allocation_lineage_invalid = any(
        "immutable candidate-artifact lineage" in reason
        for reason in bundle.reasons
    )
    if allocation_lineage_invalid:
        items.append(
            Phase9WorkItem(
                task_type="PORTFOLIO_ALLOCATION_REPAIR",
                scope="REPRODUCIBLE_CANDIDATE_PIPELINE",
                reason=(
                    "qualified portfolio-allocation evidence is missing "
                    "valid immutable candidate-artifact lineage"
                ),
                shell_command=(
                    "pio multi-pool-research "
                    "--file <POOL_INPUTS_JSON> "
                    "--equity <EQUITY> --cash <CASH> "
                    "--deployed <DEPLOYED> --drawdown-bps <BPS> "
                    "--persist-phase9-candidates "
                    "> phase9-multi-pool.json && "
                    "pio portfolio-allocation-research "
                    "--file phase9-multi-pool.json "
                    "--budget-quote <QUOTE> "
                    "--persist --require-qualified"
                ),
            )
        )

    if (
        criteria.require_contextual_bandit
        and bundle.contextual_bandit.qualified_records < 1
    ):
        cycle_id = _latest_retraining_dataset_cycle(storage)
        if cycle_id is not None:
            items.append(
                Phase9WorkItem(
                    task_type="CONTEXTUAL_BANDIT",
                    scope=cycle_id,
                    reason=(
                        "qualified contextual-bandit evidence is missing; "
                        "a checksum-bound retraining dataset is available"
                    ),
                    shell_command=(
                        "pio contextual-bandit-cycle-research "
                        "--cycle-id "
                        + _q(cycle_id)
                        + " --persist --require-qualified"
                    ),
                )
            )
        else:
            items.append(
                Phase9WorkItem(
                    task_type="CONTEXTUAL_BANDIT",
                    scope="LABELED_ACTION_CSV_REQUIRED",
                    reason=(
                        "contextual-bandit research requires a fixed fully "
                        "labeled counterfactual action CSV or persisted "
                        "retraining-cycle dataset lineage"
                    ),
                    shell_command=None,
                )
            )

    bandit_lineage_invalid = any(
        "checksum-verified retraining dataset lineage" in reason
        for reason in bundle.reasons
    )
    if bandit_lineage_invalid:
        cycle_id = _latest_retraining_dataset_cycle(storage)
        items.append(
            Phase9WorkItem(
                task_type="CONTEXTUAL_BANDIT_REPAIR",
                scope=(
                    cycle_id
                    if cycle_id is not None
                    else "RETRAINING_DATASET_REQUIRED"
                ),
                reason=(
                    "qualified contextual-bandit evidence is not bound to "
                    "valid retraining-cycle dataset lineage"
                ),
                shell_command=(
                    "pio contextual-bandit-cycle-research "
                    "--cycle-id "
                    + _q(cycle_id)
                    + " --persist --require-qualified"
                    if cycle_id is not None
                    else None
                ),
            )
        )

    if bundle.research_ready and not promotion.promotion_ready:
        if promotion.research_bundle_evidence_id is None:
            items.append(
                Phase9WorkItem(
                    task_type="PERSIST_RESEARCH_BUNDLE",
                    scope="__PHASE9_RESEARCH__",
                    reason=(
                        "current research bundle is ready but has not been "
                        "persisted as immutable evidence"
                    ),
                    shell_command=(
                        "pio phase9-research-bundle "
                        "--persist --require-ready"
                    ),
                )
            )
        elif not promotion.persisted_bundle_matches_current:
            items.append(
                Phase9WorkItem(
                    task_type="REFRESH_RESEARCH_BUNDLE",
                    scope="__PHASE9_RESEARCH__",
                    reason=(
                        "persisted research bundle is stale versus the "
                        "latest component evidence"
                    ),
                    shell_command=(
                        "pio phase9-research-bundle "
                        "--persist --require-ready"
                    ),
                )
            )

    if promotion.promotion_ready:
        items.append(
            Phase9WorkItem(
                task_type="PERSIST_PHASE9_PROMOTION",
                scope="PHASE9",
                reason=(
                    "all non-actionable research promotion gates pass"
                ),
                shell_command=(
                    "pio phase9-validate "
                    "--persist-ready --require-ready"
                ),
            )
        )

    return Phase9WorkQueue(
        phase8_promoted=phase8_promoted,
        research_bundle_ready=bundle.research_ready,
        promotion_ready=promotion.promotion_ready,
        candidate_pools=pools,
        items=tuple(items),
    )
