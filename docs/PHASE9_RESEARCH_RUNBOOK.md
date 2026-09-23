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

Phase 8 must be persistently promoted **and still current** before Phase 9 evidence can be research-qualified. Currentness rechecks the stored Phase 8 criteria against the current champion, completed-cycle lineage, continuous-promotion evidence and live champion health.

Check the prerequisite directly:

```bash
pio phase8-promotion-audit --require-current
```

A rolled-back champion, changed champion lineage, invalid promotion history, or a current live-health breach makes Phase 8 stale and blocks new Phase 9 qualification even if the historical Phase 8 promotion row still exists.

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


## Evidence progress snapshots

The work queue can persist a sanitized append-only progress snapshot:

```bash
pio phase9-work-queue --persist-snapshot
```

Snapshots store only readiness state, candidate pools, task type/scope/reason,
criteria, and a SHA-256 checksum. Emitted shell commands and RPC URLs are
deliberately excluded from persisted state.

Summarize progress across snapshots:

```bash
pio phase9-progress --require-snapshot --require-integrity
```

The progress report shows:

- first versus latest blocker count;
- blockers resolved since the first snapshot;
- blockers that appeared later;
- current Phase 8 prerequisite state;
- research-bundle readiness;
- Phase 9 promotion readiness;
- checksum integrity for every persisted snapshot.

A deployment can record these snapshots hourly with
`pio-phase9-progress.timer`. This timer is observational: it does not execute
work-queue shell commands, fetch RPC data, persist Phase 9 promotion, or make
research policy-actionable.


## Post-promotion shadow validation

A future LIVE-policy authorization must not reuse the corpus that earned the
Phase 9 research promotion. Validate a **newer**, checksum-bound retraining
cycle separately:

```bash
pio phase9-shadow-validate \
  --cycle-id <POST_PROMOTION_CYCLE_ID> \
  --persist \
  --require-ready
```

The shadow gate requires:

- the persisted Phase 9 promotion to still be current;
- the retraining dataset to pass the existing absolute-file, no-symlink and
  SHA-256 lineage checks;
- the dataset cutoff to be strictly after the Phase 9 promotion timestamp;
- the contextual-bandit replay to satisfy the shadow thresholds.

Persisted shadow evidence uses
`PHASE9_POST_PROMOTION_SHADOW_V1` and always carries
`research_only=true` and `policy_actionable=false`.

A passing shadow report is **not** LIVE-policy authorization. It is only a
separate post-promotion validation corpus that a future authorization design
may consume.


## Future LIVE-policy authorization evidence gate

The separate authorization evidence gate consumes only persisted,
deterministically replayable post-promotion shadow corpora:

```bash
pio phase9-policy-authorization-gate \
  --persist \
  --require-ready
```

Default requirements are intentionally stricter than one shadow run:

- at least 3 unique shadow cycles;
- at least 3 distinct checksum-bound dataset hashes;
- at least 3 distinct post-promotion cutoffs;
- at least 50 evaluated decisions per qualifying shadow run;
- at least 3 pools and 2 selected arms per qualifying run;
- at least 150 evaluated decisions across qualifying runs;
- non-negative mean uplift versus baseline;
- mean regret versus oracle no higher than 300 bps;
- current Phase 9 promotion;
- deterministic replay equality for every counted shadow report.

Re-persisting the same cycle does not increase the qualifying run count.
Shadow evidence evaluated under weaker criteria is not accepted by a stronger
authorization gate.

Even when `authorization_ready=true`, the report and persisted
`PHASE9_LIVE_POLICY_AUTHORIZATION_GATE_V1` evidence remain:

- `research_only=true`;
- `policy_actionable=false`;
- `execution_wired=false`.

There is intentionally no executor or LIVE-policy code path that consumes this
gate yet. A later explicit wiring change would need its own source-of-truth
update, tests, rollback design and controlled validation.


Audit the latest persisted authorization evidence against fresh deterministic
replay:

```bash
pio phase9-policy-authorization-audit --require-current
```

The audit reconstructs the stored gate criteria and recomputes the gate from
current Phase 9 plus current shadow data. A formerly ready record becomes stale
if Phase 9 is no longer current, a shadow dataset changes, replay no longer
matches, or the aggregate thresholds no longer pass. Historical evidence stays
append-only.


## Controlled policy holdout validation

Authorization evidence must survive a fresh holdout that did not contribute to
the authorization gate itself. Run a checksum-bound cycle whose dataset cutoff
is after the authorization evidence was created:

```bash
pio phase9-policy-controlled-validate \
  --cycle-id <FRESH_HOLDOUT_CYCLE_ID> \
  --persist \
  --require-ready
```

The controlled validation requires:

- the latest persisted authorization evidence to still pass its currentness
  audit;
- a cycle ID that was not counted by the authorization evidence;
- a dataset SHA-256 that was not counted by the authorization evidence;
- a dataset cutoff at least one second after the authorization evidence
  creation timestamp;
- a replay-qualified shadow report meeting the controlled thresholds (50
  decisions, 3 pools, 2 selected arms, non-negative uplift and at most 250 bps
  mean regret by default).

Persisted `PHASE9_POLICY_CONTROLLED_VALIDATION_V1` evidence is deliberately
`research_only=true`, `simulation_only=true`,
`policy_actionable=false` and `execution_wired=false`. It cannot authorize
an order or alter the Rust executor.

Audit persisted controlled validation against current replay with:

```bash
pio phase9-policy-controlled-audit --require-current
```

A change to authorization currentness, holdout lineage or deterministic replay
makes the controlled evidence stale without rewriting its historical row.
Passing this stage still does not permit LIVE policy use; any future executor
wiring needs a separately documented, bounded and reversible design.


For one fail-able check across both future-policy evidence layers:

```bash
pio phase9-policy-readiness-audit --require-ready
```

This command requires the persisted authorization evidence to remain current
and the controlled holdout evidence to remain current at the same time. Its
output is still `research_only=true`, `simulation_only=true`,
`policy_actionable=false` and `execution_wired=false`. A ready result is a
validation milestone only, not permission to submit or alter LIVE positions.


## Bounded rollout simulation

The next safety check is still non-executable. Compare a proposed Phase 9
canary envelope against the current controlled-live envelope:

```bash
pio phase9-policy-rollout-simulate \
  --file config/phase9_rollout_simulation.example.json \
  --persist \
  --require-ready
```

The JSON contains `current` and `proposed` objects using the controlled-live
limit fields. The simulator requires current Phase 9 policy-readiness evidence
and, by default:

- the proposed pool allowlist must be a subset of the current allowlist;
- every proposed numeric risk/capital cap must be less than or equal to the
  current controlled-live cap;
- the proposed envelope must be strictly narrower in at least one dimension;
- the proposal must keep `enabled=false`;
- EXIT must remain available for risk reduction;
- REBALANCE must remain disabled for the simulation canary.

Persisted `PHASE9_POLICY_ROLLOUT_SIMULATION_V1` evidence is
`research_only=true`, `simulation_only=true`,
`policy_actionable=false` and `execution_wired=false`. The example JSON is
only a shape/example; its numeric values are not production recommendations.

Audit the persisted simulation against current readiness with:

```bash
pio phase9-policy-rollout-audit --require-current
```

This stage proves only that a narrower disabled envelope is internally
consistent with the existing controlled-live limits. It does not modify Rust
configuration, enable submission, choose trades or authorize capital.
