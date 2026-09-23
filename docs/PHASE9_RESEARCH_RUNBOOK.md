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

Collect source history with the bounded read-only capture path:

```bash
pio phase9-wallet-flow-capture-run \
  --pool <POOL> \
  --historical-signature-limit 25 \
  --require-ready
```

The collector starts from current on-chain `PositionV2` accounts, expands
those current owners through Meteora `status=all` position PnL when enabled,
and can additionally inspect recent Solana signatures that mention the exact
pool account. The Rust scanner decodes only Meteora liquidity/fee/reward events
whose `lb_pair` exactly equals the requested pool and returns their historical
position/owner candidates.

For live research, the collector rescans one recent signature page for newly
appearing activity and advances at most one persisted older backfill page per
run. Inspect or advance that backfill independently with:

```bash
pio phase9-pool-activity-discovery \
  --pool <POOL> \
  --limit 25 \
  --advance-backfill
```

Historical signature discovery is never used for `--as-of` capture because
discovering an old cohort from information visible only today would be
lookahead. The scan remains bounded by RPC history retention and configured
page limits, so it improves coverage but is not a complete historical census.

After source coverage is sufficient:

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

The preferred Phase 9 qualification path remains a checksum-verified
continuous-retraining action dataset when one already exists:

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

When continuous retraining is not due and therefore no cycle dataset exists,
Phase 9 can derive an equivalent research-only counterfactual dataset from the
already validated explicit pool inputs and immutable chain/bin history:

```bash
pio phase9-bandit-research-run --persist --require-qualified
```

This path requires at least three explicit pool inputs. It uses the latest
common observation across those pools as the no-lookahead cutoff, keeps only
the latest 96 source observations per pool, writes a deterministic
checksum-named CSV beside the research database by default, persists a
`PHASE9_BANDIT_DATASET_V1` artifact, then rebuilds the dataset from persisted
sources before bandit evidence can replay-verify. The 96-observation cap keeps
scheduled refresh bounded while still yielding up to 84 labeled decision
points per pool under the default 12-observation lookback and two-observation
forward label.

A free-form fixed CSV can still be replayed for exploratory research:

```bash
pio contextual-bandit-research \
  --file <ML_ACTION_DATASET_CSV> \
  --persist
```

However, the Phase 9 research-bundle gate requires qualified contextual-bandit\nevidence with checksum-verified dataset lineage: either the persisted\ncontinuous-retraining dataset or the deterministic Phase 9 research dataset\ndescribed above. Arbitrary CSV evidence cannot satisfy promotion readiness.

Selection is strictly sequential: only rewards revealed by prior selected
actions may update the pool/context-local UCB statistics. Current-decision
oracle rewards are used only after selection for evaluation. Adversarial tests
lock this no-lookahead property.

## Ongoing chain-history sampling

The 43-observation default history requirement is a qualification floor, not a
collection ceiling. The unattended `phase9-source-capture-run` path continues
sampling the established Phase 9 pool cohort after that floor is reached,
subject to the same per-pool minimum observation interval. This gives adaptive,
regime, portfolio and derived-bandit research new market regimes to evaluate
instead of freezing their chain corpus at first qualification.

A direct `phase9-chain-history-run` remains deficit-only by default. To request
the same post-threshold behavior manually:

```bash
pio phase9-chain-history-run --continue-sampling-when-ready
```

This remains read-only chain inspection plus local append-only ingestion. It
does not authorize or execute a trade.

## Source freshness

Deterministic replay answers a different question from source freshness. A
report can still reproduce perfectly from its old immutable lineage while newer
chain, mint, wallet-flow or dataset observations are already available.

Inspect that state directly:

```bash
pio phase9-source-freshness
```

Use `--require-current` when an operational check should fail until every
research family has incorporated its newest persisted source watermark.

Freshness compares source timestamps first. Database IDs are only a
same-timestamp tie-break, so a historical backfill inserted later does not make
current research look stale merely because its SQLite row ID is larger. When a
genuinely newer source exists, `phase9-research-refresh-run` recomputes the
affected family even if its previous evidence still passes deterministic replay.
The work queue surfaces this as `RESEARCH_SOURCE_REFRESH`, and progress reports
`SOURCE_REFRESH_PENDING` until the next queue snapshot shows the refresh
resolved.

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
- contextual-bandit evidence resolves to either the persisted retraining-cycle\n  dataset or a persisted Phase 9 bandit-dataset artifact, including version,\n  cutoff, file identity, checksum and explicit-input lineage.

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
- contextual-bandit evidence re-hashes its bound dataset bytes and reruns the\n  bandit with persisted criteria; Phase 9-derived datasets additionally rebuild\n  from the exact explicit-input artifact and chain history at the stored cutoff.

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


## Rollback-trigger simulation

Rollback thresholds are explicit inputs rather than hidden defaults. Provide a
JSON object containing `metrics` and `criteria`:

```bash
pio phase9-policy-rollback-simulate \
  --file config/phase9_rollback_simulation.example.json \
  --persist
```

The simulator distinguishes three states:

- `NO_ROLLBACK_TRIGGER`: the rollout simulation is current, the configured
  minimum sample depth is met, and no configured trigger is breached;
- `OBSERVATION_PENDING`: the rollout simulation is current, no hard trigger is
  breached, but the configured minimum sample depth is not yet met;
- `ROLLBACK_REQUIRED`: the rollout simulation is stale, an explicit hard
  trigger is breached, or a configured performance floor is breached after the
  minimum sample depth.

Hard triggers can cover realized loss, drawdown, reconciliation failures,
unvalued closed positions, stale decisions and policy errors. Optional
performance triggers can cover win rate and mean return, but they are evaluated
only after the user-supplied minimum observation/closed-position/pool depth.

Persisted `PHASE9_POLICY_ROLLBACK_SIMULATION_V1` evidence remains
`research_only=true`, `simulation_only=true`,
`policy_actionable=false` and `execution_wired=false`. The example values
are illustrative test inputs, not production thresholds or return targets.

Audit replay/currentness with:

```bash
pio phase9-policy-rollback-audit --require-current
```

The simulator never closes a position, changes a policy, edits the rollout
envelope or calls the Rust executor.


## Final pre-wiring audit

For one fail-able check across the entire future-policy simulation boundary:

```bash
pio phase9-policy-prewire-audit --require-ready
```

The audit reports ready only when all of these are true at the same time:

- Phase 9 append-only storage integrity is verified;
- Phase 9 authorization evidence is current;
- the fresh controlled holdout is current;
- the disabled bounded-rollout simulation is current;
- the rollback simulation is current;
- the rollback simulation has resolved to `NO_ROLLBACK_TRIGGER` rather than
  `OBSERVATION_PENDING` or `ROLLBACK_REQUIRED`.

The output remains `research_only=true`, `simulation_only=true`,
`policy_actionable=false` and `execution_wired=false`. A ready pre-wiring
audit is the end of the simulation evidence chain, not permission to connect
Phase 9 to the LIVE executor.


## Immutable pre-wiring manifest

Once the pre-wiring audit is ready, bind the exact current component evidence
into one append-only manifest:

```bash
pio phase9-policy-manifest --persist --require-ready
pio phase9-policy-manifest-audit --require-current
```

The manifest records the latest authorization, controlled-validation,
bounded-rollout and rollback evidence IDs plus SHA-256 hashes of each normalized
evidence payload. It also carries a deterministic manifest SHA-256 over those
component references.

The manifest can persist only when the full pre-wiring audit is ready. Any newer
component evidence changes the current component identity and makes the old
manifest stale automatically. The work queue surfaces
`PERSIST_PREWIRE_MANIFEST` only after pre-wiring readiness is already current,
and `phase9-progress` reports `PREWIRE_MANIFEST_CURRENT` as the highest
simulation-only checkpoint.

This manifest remains `research_only=true`, `simulation_only=true`,
`policy_actionable=false` and `execution_wired=false`. It is an immutable
evidence identity, not an execution authorization.


## Bounded source-acquisition pass

For a single manual pass across the source families that can be collected
without economic assumptions:

```bash
SOLANA_RPC_URL=<RPC_URL> \
pio phase9-source-capture-run
```

One pass may:

1. refresh public Meteora API pool/history discovery;
2. capture enough new read-only pool snapshots to reach the chain-pool target;
3. add at most one fresh chain-history observation per currently deficient
   selected pool;
4. refresh missing/stale authoritative mint accounts; and
5. collect a bounded current-position cohort of official position histories for
   the required wallet-flow pools.

Each family is isolated: a failed API/RPC/history source is reported without
turning the other source families into synthetic success. At the end, readiness
is recomputed from persisted local state and reported as
`automatic_source_ready`.

Use `--skip-api-refresh` when API discovery is already current, and
`--require-automatic-ready` when the command should exit non-zero until chain
history, mint inputs and wallet-flow source thresholds are all satisfied.

This command performs source acquisition only. It does not run adaptive,
mint-risk, wallet-flow, hedge, allocation or bandit research; it does not
persist a Phase 9 bundle/promotion; and it cannot sign or submit Solana
transactions.

History capture now has an explicit cadence guard. The CLI defaults to a hard
minimum of 3,600 seconds between persisted chain-history observations for the
same pool. A retry inside that interval is reported as `SKIPPED_INTERVAL`;
the Rust inspector is not called and the observation count does not advance.

For unattended evidence accumulation, the repository includes
`pio-phase9-source-capture.service` and
`pio-phase9-source-capture.timer`. The timer activates every 70 minutes while
the service retains the 3,600-second hard minimum. The extra ten minutes keeps
RPC/API execution jitter from turning an intended hourly research cadence into
near-duplicate observations. This scheduled path remains source-only,
read-only and non-actionable.

## Replay-aware automatic research refresh

Once persisted sources are available, refresh the research families that do not
require new economic assumptions with:

```bash
pio phase9-research-refresh-run
```

The pass is deliberately narrower than the full work queue. It may recompute
adaptive/regime, mint-risk, wallet-flow and checksum-bound contextual-bandit
research. It first requires verified Phase 9 append-only storage and a current
Phase 8 promotion. Each family is skipped when its latest required qualified
evidence already passes deterministic replay.

For a missing or non-replay-verified family, the pass checks that family's
persisted source gate before evaluation. Source-not-ready families are reported
without fabricating evidence. When evaluation does run, the normalized report
is compared with the latest append-only evidence row and is persisted only when
it changed.

Mint-risk uses a deterministic source-derived evaluation cutoff: the latest
timestamp among the selected pool snapshots and required mint snapshots. It
does not use a fresh wall-clock cutoff on every refresh, so identical source
state does not create evidence churn.

If all Phase 9 research families—including the separately supplied static hedge
and portfolio-allocation evidence—are ready after refresh, the command persists
the checksum-bound research bundle unless `--no-persist-bundle` is supplied.
It never persists Phase 9 promotion and never changes policy. Use
`--require-automatic-ready` for a fail-able check of the automatic research
families, or `--require-bundle-ready` when the complete bundle must be ready.

Static hedge and portfolio allocation remain outside this automatic pass because
their economics depend on the explicit checksum-bound input artifact described
above.


An optional hardened systemd unit can run this refresh after the source
collector:

```bash
sudo systemctl enable --now pio-phase9-research-refresh.timer
```

The research timer uses the same 70-minute recurrence as source capture but a
25-minute boot offset, ten minutes after the source timer's default boot offset.
It never uses a `--require-*` readiness flag, because temporarily incomplete
real evidence is expected while the corpus accumulates. The service does not
invoke promotion or any LIVE/policy command.

## End-to-end Phase 9 work queue

`pio phase9-work-queue` is the single dependency-aware planner for both the
research milestone and the future-policy simulation evidence chain. It does not
skip prerequisites.


`pio phase-status` also exposes this chain under Phase 9 as
`future_policy_simulation`, including authorization currentness, controlled
holdout currentness, rollout simulation currentness, rollback state and final
pre-wiring readiness. These fields are observational only and remain explicitly
non-actionable and execution-disconnected.

Before Phase 9 research promotion is current, it continues to surface storage,
Phase 8, research-family, bundle and promotion blockers. After Phase 9
promotion is current, the same queue advances in order through:

1. post-promotion shadow evidence;
2. replay-current authorization evidence;
3. fresh independent controlled holdout evidence;
4. disabled bounded-rollout simulation;
5. rollback simulation and observation depth/remediation;
6. final pre-wiring readiness.

When a required artifact can be produced reproducibly from existing persisted
inputs, the queue emits the concrete CLI command. When a genuinely new external
artifact is required—such as a fresh retraining cycle, rollout envelope or
updated rollback metrics—it says so instead of fabricating one.


### Read-only chain capture planning

If fewer than three chain-observed pools exist, the work queue now checks
already-persisted Meteora API pool discovery. When uncaptured candidates exist,
it emits `CHAIN_POOL_CAPTURE_PLAN`; otherwise it emits
`API_POOL_DISCOVERY` with `pio collect-once`.

Build the concrete capture plan with:

```bash
pio phase9-chain-capture-plan --rpc-url <RPC_URL> --require-ready
```

The planner never performs an RPC call itself. It ranks the latest API snapshot
per pool deterministically by available TVL, then volume and address, excludes
pools that already have chain snapshots, and emits commands shaped as:

```bash
meteora-executor inspect-pool <RPC_URL> <POOL> <BIN_ARRAY_RADIUS> \
  | pio ingest-chain-snapshot --file -
```

This path is read-only with respect to Solana. It requires no wallet key,
cannot sign or submit transactions, and only persists the returned inspection
snapshot locally for research evidence.


To execute the currently selected capture candidates manually through the same
hardened Rust inspector wrapper used by PAPER chain refresh:

```bash
SOLANA_RPC_URL=<RPC_URL> \
pio phase9-chain-capture-run --require-target
```

Optional `--rust-binary-path` or `--rust-manifest-path` selects the local
read-only inspector implementation, while `--timeout-seconds` bounds each
capture. The batch re-runs the deterministic planner first, attempts only those
candidate pools, verifies that the Rust response carries the expected
`pool_address`, ingests successful snapshots, isolates failures per pool and
reports whether the target chain-pool count was reached.

This command is manual by design. The work queue emits the plan step, not the
capture-run step, so evidence acquisition does not start implicitly.


### Fresh authoritative mint inputs

Mint-risk evidence has a freshness boundary, not just an existence check. The
default mint-risk criterion accepts a required mint snapshot only when it is no
more than 3,600 seconds old at the evaluation cutoff.

Inspect exact mint input state for the selected Phase 9 pools with:

```bash
pio phase9-mint-capture-plan --target-pools 2
```

Use `--pools <POOL_A,POOL_B>` to bind the plan to an exact pool set instead
of automatic highest-depth selection. Shared token/reward mints are
deduplicated across those pools.

Capture only missing or stale mint accounts through the read-only Rust path:

```bash
SOLANA_RPC_URL=<RPC_URL> \
pio phase9-mint-capture-run --target-pools 2 --require-ready
```

The Rust executor uses `inspect-mint-env`, so the RPC URL stays in the
environment rather than process arguments. Python validates that each returned
`mint_address` matches the requested mint before append-only local ingestion.
Per-mint failures are isolated.

`phase9-work-queue` uses the same planner. For a live queue it emits one
exact-pool `phase9-mint-capture-run` task whenever required mint inputs are
missing or stale, and emits `mint-risk-research` only after those inputs are
current. An optional `phase9-work-queue --as-of <TIME>` constrains chain, mint and wallet source tasks to that cutoff. If authoritative mint state was missing or stale at that
historical cutoff, the queue refuses to propose a later capture as a backfill;
future state cannot prove past state.

### Wallet-flow source acquisition

Wallet-flow qualification depends on immutable `position_event_history` rows.
The default research window is the latest 500 events for a pool, with at least
20 events and 5 distinct users required before concentration checks can even
qualify.

Discover the pool's **current on-chain PositionV2 cohort** through the read-only
Rust scanner:

```bash
SOLANA_RPC_URL=<RPC_URL> \
pio phase9-position-discovery --pool <POOL> --limit 250
```

The Rust command filters Meteora program accounts at the RPC layer by the
`PositionV2` discriminator and the decoded `lb_pair` field, then verifies
every returned account belongs to the requested pool. It exposes only public
position/owner metadata and has no wallet, signing or submission path.

Collect official Meteora lifecycle history for that bounded cohort with:

```bash
SOLANA_RPC_URL=<RPC_URL> \
pio phase9-wallet-flow-capture-run --pool <POOL> --require-ready
```

The collector starts from current on-chain PositionV2 addresses and owners.
By default, it then expands up to 25 of those current owners through Meteora's
pool position-PnL endpoint with `status=all`, following at most 3 pages per
owner. That adds both open and closed positions for the discovered owners
without guessing addresses. Candidates are round-robin ordered across owners so
one wallet with many old positions cannot consume the full per-run position
budget. Per-owner expansion failures and per-position history failures are
isolated.

Use `--skip-owner-position-expansion` to keep the original current-position
cohort only, or tune `--owner-expansion-limit` and
`--owner-position-max-pages` to bound the public API work. The all-in-one
source pass exposes the equivalent
`--skip-wallet-owner-position-expansion`,
`--wallet-owner-expansion-limit` and
`--wallet-owner-position-max-pages` controls.

The collector persists only normalized real lifecycle events through the
existing idempotent event store and stops once the event/user source threshold
is satisfied. Source readiness is computed from the same latest-
`lookback_events` window used by `wallet-flow-research`, not lifetime
totals.

A successful owner expansion is labeled
`CURRENT_OWNER_ALL_POSITION_COHORT`; otherwise the scope remains
`CURRENT_ONCHAIN_POSITION_COHORT`. Neither label means a complete historical
pool census. Owners who have no current on-chain PositionV2 account can still
be absent even if they had closed positions in the past. If the bounded cohort
cannot reach source thresholds, the command reports that limitation rather than
synthesizing users/events.

Meeting source counts is only an input gate. It does **not** qualify wallet
flow. `wallet-flow-research` still applies the configured user-concentration
check, Phase 8 currentness, immutable source lineage and deterministic replay
requirements before evidence can enter the Phase 9 bundle.

### Explicit hedge and portfolio assumptions

Static hedge and portfolio research cannot safely infer the market/capital
assumptions that determine their economics. Those values now move through one
checksum-bound input artifact instead of shell placeholders.

Generate a template from the current research pools:

```bash
pio phase9-research-input-template \
  --pools <POOL_A,POOL_B,POOL_C> \
  > phase9-research-inputs.json
```

The template pre-fills only methodological defaults already defined in code.
It intentionally leaves external economic inputs null, including token amounts,
hedge instrument/venue, available hedge liquidity, funding, round-trip cost,
equal requested quote, per-pool network cost, account equity/cash/deployed
state, drawdown and allocation budget. The template is not valid until those
values are explicitly supplied.

Validate and persist the completed assumptions:

```bash
pio phase9-research-inputs-ingest \
  --file phase9-research-inputs.json
```

The normalized input set is stored append-only as
`PHASE9_EXPLICIT_RESEARCH_INPUTS_V1` with a deterministic SHA-256 and the
hard boundary `research_only=true`, `policy_actionable=false`,
`execution_wired=false`.

Audit the latest artifact before execution with:

```bash
pio phase9-research-inputs-audit --require-valid
```

The audit fails closed on a missing/mismatched SHA, invalid normalized inputs, a boundary violation, the wrong status, or any input artifact incorrectly marked as qualified research. The work queue uses the same audit and routes invalid latest artifacts back to template/ingest repair rather than executing them.

Run the research directly from the persisted artifact:

```bash
pio phase9-explicit-research-run --persist --require-ready
```

This replays static-hedge research from the exact instrument/cost assumptions,
builds the multi-pool comparison from the exact account/pool inputs, persists
the portfolio candidate artifact, binds that candidate artifact back to the
explicit-input evidence ID/SHA, then evaluates and persists portfolio
allocation. No input file is reinterpreted after persistence.

When hedge or allocation evidence is missing, `phase9-work-queue` now emits
`EXPLICIT_RESEARCH_INPUTS` if no artifact exists, or
`EXPLICIT_RESEARCH_RUN` with the exact evidence ID once a valid artifact has
been persisted.

### Exact adaptive/regime history depth

Distinct pool coverage is only the first requirement. Phase 9 adaptive
walk-forward needs enough time-separated chain observations per selected pool.

Inspect the exact current deficits with:

```bash
pio phase9-chain-history-plan
```

The planner derives the requirement from the same criteria used by the
evaluator. For the defaults:

- holding window = 6 observations;
- minimum historical displacement windows = 12;
- minimum walk-forward decisions = 20;
- regime minimum = 16 observations.

A valid adaptive decision first becomes possible after enough trailing history
to form 12 six-observation displacement windows, and every evaluated decision
also needs six future observations. The resulting minimum is:

`20 + (2 × 6) + 12 - 1 = 43` chain observations per pool.

The default multi-pool gate also requires at least 3 pools and a qualified-pool
rate of 0.67. At the minimum 3-pool set, 2/3 is only 66.67%, so all 3 selected
pools must be history-capable to satisfy that rate.

Capture one fresh observation for each deficient selected pool with:

```bash
SOLANA_RPC_URL=<RPC_URL> \
pio phase9-chain-history-run
```

One invocation takes at most one new read-only snapshot per deficient pool.
Repeat it over real elapsed time until `phase9-chain-history-plan
--require-ready` passes. An explicit `--observed-at` must be timezone-aware
and strictly newer than the latest persisted snapshot for every captured pool;
stale/equal timestamps fail closed instead of inflating history depth.

Once exact history depth is ready, `phase9-work-queue` stops emitting
`CHAIN_HISTORY_DEPTH` and unlocks the concrete
`phase9-research-validate` command.

Persisting the queue snapshot with `--persist-snapshot` records the new
policy-evidence readiness fields as sanitized append-only state. No emitted
shell commands, RPC URLs or secrets are stored. `pio phase9-progress` now
reports the highest reached checkpoint, including
`POLICY_AUTHORIZATION_CURRENT`, `CONTROLLED_VALIDATION_CURRENT`,
`ROLLOUT_SIMULATION_CURRENT`, `ROLLBACK_SIMULATION_CURRENT` and
`PREWIRE_READY`.
