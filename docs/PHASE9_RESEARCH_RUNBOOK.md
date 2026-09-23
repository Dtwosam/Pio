# Phase 9 Research Runbook

Status: research implementation complete; real qualified research-bundle evidence pending.

## Purpose

Phase 9 is an advanced-edge research phase.

Its outputs are intentionally non-actionable:

- `research_only=true`;
- `policy_actionable=false`;
- a persisted Phase 9 promotion record is a research milestone only;
- Phase 9 promotion evidence must remain `research_only=true` and `policy_actionable=false`;
- no Phase 9 report or promotion record can authorize Rust signing/submission.

A qualified Phase 9 research bundle means the research corpus is internally
complete enough for review. It does **not** mean Phase 9 may affect LIVE policy.

## Prerequisite

Phase 8 must be persistently promoted before Phase 9 evidence can be
research-qualified.

## Research families

### Adaptive range + regime

Evaluate multiple pools with no-lookahead adaptive-range walk-forward evidence
and DLMM regime classification:

```bash
pio phase9-research-validate \
  --pools <POOL_A,POOL_B,POOL_C> \
  --persist \
  --require-qualified
```

### Authoritative mint risk

Collect mint state directly from Solana with the read-only Rust executor:

```bash
meteora-executor inspect-mint <RPC_URL> <MINT_ADDRESS> > mint.json
pio mint-snapshot-ingest \
  --file mint.json \
  --observed-at <CAPTURE_TIME>
```

Repeat for token X, token Y and relevant reward mints, then evaluate one pool:

```bash
pio mint-risk-research \
  --pool <POOL> \
  --persist \
  --require-qualified
```

By default, qualification requires initialized supported mints, revoked
mint/freeze authorities, fresh no-lookahead evidence and no unapproved
Token-2022 extension bytes.

### Wallet-flow research

```bash
pio wallet-flow-research \
  --pool <POOL> \
  --persist \
  --require-qualified
```

This is descriptive research. Minimum history/user diversity and maximum
top-wallet concentration are required before evidence qualifies.

### Portfolio allocation research

Build a fixed candidate JSON corpus and evaluate capped quote allocation:

```bash
pio portfolio-allocation-research \
  --file <CANDIDATES_JSON> \
  --budget-quote <QUOTE> \
  --persist \
  --require-qualified
```

Allocation remains research-only and is bounded by position count,
per-pool concentration, candidate quality and minimum budget utilization.

### Static hedge research

Hedge research requires explicit market assumptions rather than assuming an
unlimited frictionless hedge exists:

```bash
pio static-hedge-research \
  --pool <POOL> \
  --amount-x <ATOMIC_X> \
  --amount-y <ATOMIC_Y> \
  --hedge-instrument-id <INSTRUMENT> \
  --hedge-venue <VENUE> \
  --hedge-available-liquidity-y-atomic <LIQUIDITY> \
  --hedge-max-liquidity-share-bps 1000 \
  --hedge-max-leverage 1.0 \
  --hedge-funding-bps-per-window <BPS> \
  --hedge-round-trip-cost-bps <BPS> \
  --persist \
  --require-qualified
```

Research qualification fails if the modeled hedge exceeds the configured
liquidity-share or leverage assumptions.

### Contextual bandit replay

The preferred Phase 9 qualification path is bound to a checksum-verified
continuous-retraining action dataset:

```bash
pio contextual-bandit-cycle-research \
  --cycle-id <RETRAINING_CYCLE_ID> \
  --persist \
  --require-qualified
```

The command resolves the cycle's persisted `CONTINUOUS_RETRAIN_DATASET_V1`
evidence, verifies the dataset path, SHA-256, dataset version and cycle cutoff,
then refuses any file whose decision or forward-label timestamp exceeds that
cutoff. Persisted bandit evidence records this lineage.

A free-form fixed CSV can still be replayed for exploratory research:

```bash
pio contextual-bandit-research \
  --file <ML_ACTION_DATASET_CSV> \
  --persist
```

However, the Phase 9 research-bundle gate requires qualified contextual-bandit
evidence with checksum-bound retraining-cycle lineage by default. Arbitrary CSV
evidence cannot satisfy promotion readiness.

Selection is strictly sequential: only rewards revealed by prior selected
actions may update the pool/context-local UCB statistics. Current-decision
oracle rewards are used only after selection for evaluation. Adversarial tests
lock this no-lookahead property.

## Evidence work queue

Before manually inspecting every research family, build the current evidence
queue from the persisted database:

```bash
pio phase9-work-queue
```

If an RPC endpoint may be embedded in generated authoritative mint-inspection
commands:

```bash
pio phase9-work-queue --rpc-url <RPC_URL>
```

The queue is dependency-aware. For example, a pool missing authoritative mint
snapshots receives `MINT_SNAPSHOT` tasks first; it does not prematurely emit
`mint-risk-research`. Hedge, portfolio-allocation and contextual-bandit tasks
remain explicit about the external artifact or market assumptions still
required instead of inventing them.

When all component evidence is qualified, the queue advances to persisting or
refreshing the research bundle, then to the non-actionable Phase 9 promotion.

## Research-bundle gate

After the individual research families have persisted qualified evidence:

```bash
pio phase9-research-bundle --persist --require-ready
```

Default bundle requirements include:

- persisted Phase 8 promotion;
- at least one qualified adaptive/regime multi-pool report;
- at least 2 qualified mint-risk pools;
- at least 2 qualified wallet-flow pools;
- at least one qualified portfolio-allocation report;
- at least 1 qualified static-hedge pool;
- at least one qualified contextual-bandit replay;
- every latest evidence record preserves
  `research_only=true` and `policy_actionable=false`.

The bundle uses the latest persisted evidence per research family/pool. A newer
record that violates the research-only boundary invalidates readiness even if
an older record qualified.

## Research-only Phase 9 promotion

Once the bundle is ready, Phase 9 can persist a research milestone:

```bash
pio phase9-validate --persist-ready --require-ready
```

This writes `PHASE9_PROMOTION_V1` only when Phase 8 is promoted and the
research bundle is ready. Promotion additionally requires an immutable
persisted research-bundle record whose component evidence IDs and criteria
still match the latest evidence. If newer component research appears, the old
bundle becomes stale and must be revalidated/re-persisted first. The persistence
function rejects reports that are policy-actionable or not research-only. `pio phase-status` therefore shows
Phase 9 with explicit `research_only=true` and
`policy_actionable=false`.

A persisted Phase 9 research promotion does not alter the Rust executor,
controlled-live authorization, deterministic/ML champion policy, position
sizing, or submission gates.

## What remains after bundle readiness

Phase 9 research-bundle readiness or research promotion does not authorize
deployment.

Before any Phase 9 signal could influence LIVE policy, a separate future design
must define:

- which Phase 9 outputs are eligible for policy use;
- real evidence thresholds and diversity requirements;
- explicit comparison against the current promoted baseline/champion;
- rollback conditions;
- bounded capital/risk effects;
- a new **LIVE-policy** promotion/authorization path that is separate from the
  research-only `PHASE9_PROMOTION_V1` milestone.

Until such a design is implemented and validated, Phase 9 stays research-only.
