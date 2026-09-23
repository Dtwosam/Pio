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

The preferred qualification path first persists the exact multi-pool candidate
artifact and its account-state/replay assumptions:

```bash
pio multi-pool-research \
  --file <POOL_INPUTS_JSON> \
  --equity <EQUITY> \
  --cash <CASH> \
  --deployed <DEPLOYED> \
  --drawdown-bps <BPS> \
  --persist-phase9-candidates \
  > phase9-multi-pool.json

pio portfolio-allocation-research \
  --file phase9-multi-pool.json \
  --budget-quote <QUOTE> \
  --persist \
  --require-qualified
```

The candidate artifact is stored immutably with a SHA-256 over its source
inputs, account assumptions and ranked comparison. Allocation evidence carries
that evidence ID/hash. Phase 9 bundle readiness requires the reference to
resolve back to the immutable research-only candidate artifact.

Free-form candidate JSON remains useful for exploratory allocation runs, but
provenance-free allocation evidence cannot satisfy Phase 9 promotion readiness.

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

## Immutable lineage requirements

Qualified component evidence must remain reproducible from persisted source
records:

- adaptive range and regime reports bind the exact chain-pool snapshot IDs and
  deterministic active-bin source hashes;
- mint-risk reports resolve their pool snapshot and every assessed mint snapshot
  ID to the authoritative stored rows;
- wallet-flow reports bind the exact position-event IDs and SHA-256 of the
  event fields used by the analysis;
- static-hedge reports bind each pool snapshot to the exact active-bin
  liquidity/price snapshot and SHA-256 the full price path;
- portfolio candidate evidence recomputes its SHA-256 from persisted source
  inputs, account assumptions and comparison payload; matching stored labels
  alone are insufficient;
- contextual-bandit evidence resolves to the persisted retraining-cycle dataset
  evidence, version, cutoff, file identity and checksum.

The work queue emits lineage-repair tasks when reproducible source evidence
exists. It does not invent missing hedge assumptions or authoritative chain
inputs.

## Append-only evidence storage

Phase 9 replay depends on historical evidence being stable after capture.

SQLite enforces immutability for the authoritative source tables used by Phase
9 replay: chain-pool snapshots, token-mint snapshots, bin-liquidity snapshots
and position-event history. It also enforces immutability for
`advanced_edge_evidence`. Re-running research creates a new evidence row; it
never edits the prior one.

Phase promotion keeps a current per-phase pointer for operational status, while
every write is also appended to immutable
`phase_promotion_evidence_history`. This makes stale/current promotion
transitions auditable without sacrificing the ability to refresh the current
promotion after a new verified bundle.

Contextual-bandit qualification also refuses retraining dataset paths that are
relative, symlinked or missing. The exact regular-file bytes must continue to
match the persisted SHA-256 and dataset version.

## Deterministic replay requirements

Immutable source IDs and hashes are necessary but not sufficient. A qualified
Phase 9 report must also reproduce its derived metrics from the stored source
corpus:

- adaptive/regime evidence is rerun through the original multi-pool evaluator
  with its persisted research, adaptive, validation and regime criteria;
- mint-risk evidence is rerun against the exact content-hashed pool/mint
  snapshots with its original criteria and cutoff;
- wallet-flow evidence is rerun against the exact hashed event window;
- static-hedge evidence is rerun with the original token amounts, instrument
  assumptions, criteria and price-path cutoff;
- portfolio allocation reconstructs the immutable cross-pool candidate report
  and reruns allocation with the stored budget and criteria;
- contextual-bandit evidence re-hashes the retraining CSV bytes and reruns the
  cycle-bound bandit using the persisted criteria.

The normalized replayed report must equal the persisted qualified evidence.
Source-correct but metric-forged evidence therefore fails closed.

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
  `research_only=true` and `policy_actionable=false`;
- adaptive/regime, mint-risk, wallet-flow, static-hedge, portfolio-allocation
  and contextual-bandit evidence passes its immutable lineage verification.

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

### Phase 9 promotion currentness

A persisted `PHASE9_PROMOTION_V1` row is a historical milestone, not proof
that the underlying research corpus is still current. `pio phase-status`
reports both the persisted Phase 9 state and a `current` / `currentness`
audit. The currentness audit re-evaluates the Phase 9 promotion gate and
requires the persisted promotion report to exactly match the current,
checksum-valid research-bundle identity.

If newer component evidence appears, a source artifact changes, deterministic
replay fails, or the research bundle is refreshed, the previous promotion
becomes stale until the bundle and promotion are revalidated and persisted
again.


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


## Storage integrity preflight

Before trusting Phase 9 bundle or replay results, verify the SQLite immutability
contract:

```bash
pio phase9-storage-integrity --require-verified
```

The audit checks the required fail-closed UPDATE/DELETE triggers for Phase 9
source snapshots, position-event history, advanced-edge evidence, live-model
evidence and immutable promotion history. Phase 9 research-bundle readiness and
the deterministic replay audit both require this storage audit to pass.

A missing or malformed trigger makes Phase 9 incomplete even when the stored
research metrics themselves still replay.

## Deterministic replay audit

Phase 9 qualification must be reproducible from persisted immutable sources.
Use the read-only replay audit to inspect every required research family:

```bash
pio phase9-replay-audit --require-verified
```

The report names the latest and qualified evidence IDs for adaptive/regime,
mint-risk, wallet-flow, portfolio-allocation, static-hedge and contextual-
bandit research. A family is `REPLAY_VERIFIED` only when enough qualified
records exist, the research-only boundary is intact, and the persisted report
reproduces from its bound source lineage. Missing evidence, boundary violations
and replay mismatches fail the command when `--require-verified` is used.

This audit is diagnostic and non-actionable. It does not grant LIVE policy
authority.


## Operational audit

For one fail-able read-only check across the full persisted Phase 9 trust
boundary, run:

```bash
pio phase9-operational-audit --require-verified
```

This command requires all three conditions at the same time:

- immutable Phase 9 storage triggers are present and valid;
- every required research family deterministically replays from its persisted
  source lineage;
- the persisted `PHASE9_PROMOTION_V1` row is still current versus the latest
  checksum-valid research bundle and immutable promotion history.

The report always carries `research_only=true` and
`policy_actionable=false`. Passing this audit does not authorize any LIVE
policy change; it only proves that the persisted Phase 9 research milestone is
still internally trustworthy.

For promotion currentness alone:

```bash
pio phase9-promotion-audit --require-current
```
