# Meteora Adaptive LP Bot — Source of Truth

Status: v0.7
Date: 2026-09-23

## 1. Mission

Build an adaptive liquidity-provision bot for Meteora DLMM on Solana that learns which pools, entry times, liquidity ranges, position sizes, and exit conditions produce the best net returns while controlling downside risk.

The bot is allowed to stay in cash when no setup meets its minimum expected edge.

## 2. Primary objective

Maximize long-run net account equity, not headline APR.

Primary metric:

`Net Return = (Ending Account Equity - Starting Account Equity - Net Deposits) / Starting Account Equity`

Account equity includes:
- wallet balances
- marked value of open DLMM positions
- unclaimed and claimed fees/rewards
- realized trading PnL
- hedge PnL when hedging is enabled
- transaction, priority-fee, slippage, funding, and rebalance costs

Secondary metrics:
- realized PnL
- mark-to-market PnL
- max drawdown
- profit factor
- Sharpe-like risk-adjusted return
- fee income / capital deployed
- IL versus hold benchmark
- time in range
- rebalance cost
- win rate

## 3. Return target

20% daily is an aspirational benchmark, not a guaranteed return and not a hard trading constraint.

The bot must never increase risk simply because a daily target has not been reached. A zero-trade day is valid when expected edge is weak.

## 4. Scope

### In scope
- Meteora DLMM pool discovery
- OHLCV, volume, pool and position data ingestion
- candidate-pool filtering
- liquidity range selection
- strategy selection: Spot / Curve / BidAsk where supported
- capital allocation
- entry timing
- active position monitoring
- fee tracking
- range/rebalance decisions
- exit decisions
- PnL accounting
- historical simulation and walk-forward testing
- paper trading
- supervised ML models
- model registry and champion/challenger promotion
- online learning from completed positions
- risk engine and kill switch
- Rust execution service
- Python research/training service

### Later / optional
- perp hedging for delta reduction
- external token-risk feeds
- wallet-flow/on-chain smart-money features
- contextual bandits / reinforcement learning after the simulator is validated

## 5. What the bot learns

For every decision point, store:

### State
- pool address and token pair
- timestamp
- pool age
- token prices
- OHLCV windows
- realized volatility
- momentum
- volume and volume acceleration
- TVL / liquidity
- fee rate and fee generation
- active bin
- liquidity concentration around active bin
- recent bin movement
- current wallet balances
- current open positions
- current portfolio drawdown
- estimated transaction/slippage cost
- token-risk features when available

### Action
- skip / enter / rebalance / exit
- pool selected
- capital allocated
- lower and upper bin
- distribution strategy
- expected holding horizon
- risk limits attached to the trade

### Outcome
- fees earned
- rewards earned
- inventory value change
- transaction and rebalance costs
- IL versus hold benchmark
- realized return
- max adverse excursion
- max favorable excursion
- time in range
- final net PnL

Each completed position becomes a labelled training example.

## 6. Learning approach

### Stage A — deterministic baseline
A rules engine creates sensible candidate positions and establishes a benchmark.

### Stage B — supervised learning
Train separate models for:
- expected net return
- downside / drawdown risk
- probability a range remains productive
- expected fee generation
- expected optimal holding period

The decision engine ranks candidate actions using predicted return minus a configurable risk penalty.

### Stage C — online adaptation
New completed trades are appended to the dataset. Models retrain on a schedule or after enough new samples.

A new model is a challenger. It becomes champion only if it beats the existing model on:
- out-of-sample walk-forward data
- cost-adjusted return
- drawdown
- paper-trading performance

### Stage D — advanced policy learning
Contextual bandits or reinforcement learning may be tested only after the simulator reproduces real position outcomes within an acceptable error band.

Live-money exploration is never unrestricted.

### Phase 2 fidelity gate

Deterministic simulator math must be promoted through an explicit fail-closed gate before it can become an authoritative training-label source.

The gate separates:
- exact amount/share reconciliation
- exact fee-checkpoint reconciliation
- exact reward-checkpoint reconciliation with positive reward-growth samples
- composition-fee formula reconciliation
- rebalance lifecycle support
- real transaction-fee calibration
- add-execution calibration
- broader slippage calibration
- operator-selected minimum sample sizes and coverage

A capability that is implemented but not independently reconciled remains blocked. Missing or legacy data is ineligible; it is never treated as a zero-error sample.

For standard-SPL liquidity operations, the gate is evidence-driven. Composition-fee validation requires slot-bounded prestate whose pool and active-bin-array account histories prove no intervening state mutation, target-time fee-state replay, an exactly reconstructable active-bin allocation, and a positive emitted CompositionFee event. Add/rebalance execution calibration uses the transaction's own active-bin and token-amount guard bounds plus real Solana receipt costs.

### Phase 3+ promotion source of truth

Phase 2 and Phase 3 promotion decisions are persisted as qualified evidence records. Downstream ML qualification and deterministic paper entry read persisted promotion state; callers cannot bypass a gate by passing a readiness boolean.

Phase 3 comparison uses equal requested notional and entry-value normalized economics across pools. Promotion requires no-lookahead multi-pool validation with explicit sample, positive-edge, average-excess and downside thresholds.

ML challengers move through evidence-backed stages. Offline qualification requires persisted Phase 3 promotion and held-out challenger evidence. Champion promotion additionally requires qualified paper evidence.

Paper mode maintains a persistent, idempotent ledger. For chain-bound counterfactual positions, inventory and fee/reward accrual are derived from real DLMM bin snapshots using the same validated liquidity-share and checkpoint formulas as the simulator. Token X/Y rewards are valued directly from pool economics; external reward mints require a fresh persisted account-quote observation and otherwise fail closed. Quote lookup is no-lookahead with respect to the requested valuation time. Unsupported token programs fail closed.

Live paper cycles derive pool safety from the latest local normalized state rather than caller flags. Prepared valuations and paper events use deterministic keys so restarts cannot double-count fee/reward income or repeat an exit/rebalance. Account-level scheduling discovers open chain-bound positions, skips positions with no new chain observation or missing token-Y quote data, and can cap each cycle using oldest-last-observation priority. Newly authorized deterministic Phase 3 paper entries are preflighted and then commit the cash debit, paper position, ENTER event, and counterfactual chain binding atomically in one database transaction.

Unattended PAPER operation is driven by idempotent ticks. A tick refreshes only the market metadata needed by open positions, refreshes stale/missing chain state through the Rust executor's read-only pool inspector, optionally refreshes token-Y USD quotes through Jupiter, and then runs the persisted portfolio supervisor. A per-account SQLite lease prevents overlapping scheduler workers. Tick IDs are deterministic by time bucket, expired leases may recover stale RUNNING ticks, and production refresh can use a prebuilt Rust binary without granting Python any signing capability.

Phase 5 completion is evidence-driven. Scheduler lease acquisition, overlap, stale-worker recovery and tick completion are persisted as lifecycle events. Current health can be exported as Prometheus-compatible gauges. A configurable endurance gate evaluates accumulated runtime, terminal tick count, failure rate, dependency-blocked rate, maximum failure streak, stale RUNNING ticks, applied chain valuations and distinct valued positions. Passing this operational gate does not prove profitability; real multi-observation PAPER evidence and accounting review are still required before Phase 5 can be called complete.

Phase 5 promotion is persisted only after Phase 3 is persistently promoted, the stricter multi-day endurance gate passes, multiple positions and pools have real applied chain valuations, a minimum closed-position sample exists, and a decimal-exact audit reconciles stored PAPER account/position state back to the immutable PAPER event ledger. The persisted Phase 5 record stores the criteria and evidence used. Missing evidence keeps Phase 5 unpromoted even when all implementation code exists.

### Phase 6 execution boundary

A `LIVE` proposal is not executable merely because the strategy emitted it. The Rust executor independently applies proposal risk limits, binds the serialized transaction to the proposal pool and configured fee payer, restricts programs, binds instruction discriminators/data prefixes to the proposal action, enforces transaction-size/account constraints, rejects disallowed lookup-table usage or signed input, and simulates only after the transaction guard is persisted.

Execution intents are durable and keyed by immutable decision ID. Reusing an ID with different proposal, risk configuration or transaction policy fails closed. Risk state is monotonic. Simulation requires a persisted accepted transaction guard. Entering signing state additionally requires persisted authorization proving the isolated executor wallet is the guarded transaction fee payer.

Standard-SPL instruction construction now covers normal entry, normal rebalance, emergency exposure exit, and post-withdraw settlement. Entry uses a deterministic Meteora position PDA derived from the executor wallet and approved pool/range, initializes missing bin arrays, and remains unsigned. Rebalance consumes precomputed validated remove/add parameters rather than recalculating strategy economics inside the executor, then resolves and verifies position ownership, pool/token accounts and touched bin arrays from Solana state. Emergency exit uses Meteora `RemoveAllLiquidity`. After liquidity reaches zero, settlement claims fees and supported rewards before `ClosePosition2`; final closure is accepted into the live ledger only after a later confirmed RPC proof shows the position account is absent. Token-2022 execution remains blocked.

Before signing state, the executor now refreshes the still-unsigned transaction to a fresh confirmed blockhash, re-applies the transaction/account/instruction guard and wallet authorization to that exact message, simulates that exact blockhash without replacement, and persists both the prepared transaction and final simulation. This prevents a successful earlier simulation from authorizing a materially different final message.

Confirmation reconciliation for an already-sent signature is read-only and restart-safe: pending remains `SENT`, success becomes `CONFIRMED`, and chain failure becomes terminal `FAILED`. Expired prepared blockhashes are never blindly resubmitted; ambiguous `SENT` intents use explicit recovery. Terminal intents can export a decision-bound execution receipt containing signature, pool/action, slot, fee/compute outcome and decoded event counts. Python can ingest that receipt idempotently, reconcile it to a stored Solana transaction snapshot, decode settlement ClaimFee2/ClaimReward2/PositionClose events, derive immutable atomic execution effects, advance the live position lifecycle, prove final closure, and aggregate exact atomic closed-position outcomes without receiving wallet access.

A closed atomic outcome becomes `VALUED` only through persisted no-lookahead evidence. Every execution cashflow is valued using the most recent permitted token quote at or before that execution time, subject to a freshness bound; SOL network fees use the same rule. Principal movements, composition costs, earned fees, rewards and network costs stay separate. Missing settlement effects, reward mints, pool metadata or fresh quotes fail closed. The Rust execution journal can also export the exact terminal originating proposal context. Python stores that context immutably and reconciles it to the terminal receipt before creating a learner label that ties model version, strategy, confirmed entry range and proposed capital to realized live PnL/return and prediction error. The live execution ledger audit checks valuation and label completeness so incomplete outcomes cannot silently enter learning.

The executor has a deterministic one-signer path bound to the exact persisted presign transaction and an idempotent submission coordinator that records `SENT` plus the signature before RPC submission. Ambiguous RPC submission errors remain `SENT`, and retries can only regenerate/resubmit the same signed message/signature. Token-2022 transfer-hook construction is implemented for entry, rebalance, exit and settlement. A controlled-live submit command exists only when the binary is compiled with the non-default `live-submit` feature and additionally requires `PIO_LIVE_SUBMIT_ENABLED=1`; default builds cannot submit. Real end-to-end controlled lifecycle validation remains pending.

### Phase 7 controlled-live boundary

Controlled live authorization is a separate gate from strategy selection and the Phase 6 transaction executor. ENTER and REBALANCE require qualified Phase 5 promotion evidence, an explicit live enable flag, an allowlisted pool, hard per-entry/daily quote-capital caps, a daily ENTER-count cap, concurrent-position limits, acceptable proposal drawdown, and an independent confirmed realized-loss budget derived from quote-valued CLOSED live outcomes. If a position closed today but its immutable outcome is not yet valued, new ENTER/REBALANCE fails closed. Unresolved ENTER intents consume capacity. REBALANCE and EXIT require one unambiguous tracked active position for the pool, and rebalances are capped per position. EXIT remains independently available for risk reduction when the entry/rebalance kill switch is off, the pool was removed from the allowlist, or loss/drawdown gates block new risk.

The controlled-live check is read-only. The feature-gated submit path requires accepted Phase 5 evidence, Phase 6 readiness, the isolated wallet, strict transaction/action/account policy, exact final simulation, controlled-live authorization bound to the same decision/action/pool, and both compile-time and runtime live-submit switches. The checked-in/default build keeps submission unavailable. Controlled validation must complete before live limits are widened or unattended live execution is considered.

## 7. Decision flow

1. Discover pools.
2. Remove pools failing liquidity, data-quality, or token-risk checks.
3. Generate multiple candidate ranges/strategies for every eligible pool.
4. Estimate fees, return, downside, cost and probability of staying productive.
5. Risk engine rejects unsafe actions.
6. Rank remaining actions.
7. Enter only if expected net edge exceeds the configured threshold.
8. Monitor every open position.
9. Rebalance, reduce or exit when the expected value of staying falls below the expected value of leaving.
10. Record the complete outcome for learning.

## 8. Risk invariants

These rules override the ML model:
- no trade with stale or incomplete market data
- no transaction when proposal-to-transaction account/program/action binding fails
- no transaction when simulation fails
- no trade above configured capital-per-position limit
- no trade above configured portfolio exposure limit
- no automatic risk increase to chase a daily return target
- stop opening new positions after max daily drawdown is hit
- exit/reduce according to emergency rules when pool liquidity collapses or data becomes unreliable
- private keys never enter the ML service, logs, database or source code
- signing state requires persisted authorization of the isolated executor wallet
- live execution can be disabled independently of data collection and model training

## 9. Modes

`BACKTEST` — historical/simulated only.

`PAPER` — consumes live data and records hypothetical trades; no signing.

`LIVE` — Rust executor may sign approved transactions.

Default is `PAPER` until promotion criteria are met.

## 10. System split

### Rust executor
Responsible for:
- Solana RPC
- Meteora DLMM account reads
- transaction building
- transaction simulation
- signing
- sending
- confirmation
- position monitoring
- emergency exits
- enforcing hard risk limits

### Python learner
Responsible for:
- Meteora Data API ingestion
- historical dataset construction
- feature engineering
- simulation/backtesting
- model training
- inference/ranking
- model evaluation
- model registry
- reporting

The Python process can propose actions. The Rust risk engine has final authority over whether an action can execute.

## 11. Source interfaces verified at project start

Meteora DLMM currently provides:
- indexed pool data
- OHLCV
- historical pool volume
- portfolio and position PnL endpoints
- position event history
- wallet claim data
- Anchor event-CPI payloads for liquidity, composition-fee and rebalance flows
- Solana transaction receipts with fee and compute-unit metadata
- a Rust `commons` integration library for account decoding, PDA helpers, bin arrays, quotes and instruction construction

Meteora DLMM program ID:
`LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo`

Production Data API:
`https://dlmm.datapi.meteora.ag`

The bot must treat upstream APIs and SDKs as versioned dependencies and pin/test upgrades before production use.

## 12. Definition of success

The project is successful when it can:
1. reproduce historical DLMM position economics with reliable cost accounting;
2. identify and rank candidate positions without look-ahead bias;
3. paper trade continuously and reconcile expected versus observed outcomes;
4. enforce hard risk rules independent of the model;
5. execute a low-capital live position safely;
6. improve the champion model using new data without silently degrading live performance.

Profit is evaluated over statistically meaningful samples, not one exceptional day.


## Phase 9 advanced edge boundary

Phase 9 research qualification requires the persisted Phase 8 promotion to remain current: the current champion must still satisfy the stored Phase 8 criteria and retain the same continuous-promotion lineage. A historical Phase 8 row alone is insufficient after champion rollback, lineage change, or live-health degradation.

Phase 9 advanced-edge modules are research-only. Persisted
`PHASE9_PROMOTION_V1` is a non-actionable research-validation milestone and
must itself preserve `research_only=true` and `policy_actionable=false`.
Neither Phase 8 nor Phase 9 research promotion makes an advanced signal
actionable. Any future LIVE use requires a separate, explicitly designed
LIVE-policy authorization gate and validation corpus.

The first advanced-edge module estimates a DLMM range half-width from historical
active-bin movement available at a decision cutoff. It constructs historical
holding windows entirely before that cutoff, takes an empirical displacement
quantile for the requested coverage, and clamps it to an explicit configured
range cap. Future snapshots are excluded when `as_of` is supplied.

Adaptive-range output always carries `research_only=true` and
`policy_actionable=false`. A `RESEARCH_READY` status means the evidence is
sufficient to study; it does not authorize entry, rebalance, signing or
submission.


### Phase 9 mint and flow research boundary

Mint-risk research uses authoritative Solana mint accounts captured by the
read-only Rust executor. Python persists those snapshots immutably and evaluates
only observations available at or before the requested research cutoff.
Research checks token-program consistency with the DLMM pool, initialization,
mint/freeze authority revocation, decimal bounds, reward mints, snapshot
freshness and Token-2022 extension presence. Unknown or stale evidence fails
closed. Token-2022 extension bytes are treated conservatively unless explicitly
allowed for a research run because the base inspector does not yet classify
each extension type.

Wallet-flow research remains descriptive. It requires minimum event/user
coverage and rejects highly concentrated activity before the evidence can be
research-qualified. Portfolio allocation research is likewise non-actionable:
it caps per-pool concentration, number of positions and minimum budget
utilization. None of these Phase 9 outputs may alter LIVE policy without a
separate future promotion boundary.


### Phase 9 evidence orchestration

The Phase 9 evidence work queue inspects persisted research state and emits the
next evidence tasks without inventing missing assumptions. It stages
authoritative Rust mint inspection before mint-risk research, uses observed
chain pools for adaptive/wallet-flow tasks, and leaves hedge, allocation and
bandit artifacts explicit when they cannot be derived safely.

Phase 9 research promotion is additionally bound to an immutable persisted
research-bundle record. If newer component evidence appears, the prior bundle is
stale and must be revalidated and persisted again before
`PHASE9_PROMOTION_V1` can be written.


Phase 9 component qualification is source-lineage-bound, not merely
self-declared in evidence JSON. Adaptive/regime evidence records exact
`chain_pool_snapshots` IDs plus deterministic SHA-256 hashes of the active-bin
source windows. Wallet-flow evidence records exact `position_event_history`
IDs plus a deterministic source-event hash. Static-hedge evidence records the
paired pool-snapshot and active-bin-liquidity IDs used for every Q64 price in
the hedge path plus a deterministic path hash. Mint-risk evidence must resolve
to authoritative persisted pool/mint snapshot IDs. Portfolio candidate
artifact hashes are recomputed from the persisted source inputs, assumptions
and ranked comparison rather than trusting stored SHA labels. Contextual-bandit
evidence must resolve to the persisted retraining dataset/cycle lineage and
matching checksum. Any missing, forged, stale or mismatched lineage fails the
Phase 9 bundle closed. Qualified Phase 9 metrics are replay-verified as well:
each deterministic research family is rerun from its persisted immutable
sources, original cutoff and original criteria/assumptions, and the normalized
replayed report must equal the persisted evidence. Valid source hashes alone do
not make edited derived metrics acceptable.


Phase 9 persistence is append-only at the database boundary. Authoritative
chain-pool snapshots, token-mint snapshots, bin-liquidity snapshots and
position-event history reject UPDATE/DELETE operations. Persisted
`advanced_edge_evidence` rows are likewise immutable; corrected or newer
research is represented by a new evidence row rather than rewriting history.
The current phase-promotion pointer may be refreshed after revalidation, but
every promotion write is also copied into immutable
`phase_promotion_evidence_history` so prior promotion states remain auditable.

Checksum-bound contextual-bandit retraining datasets must use an absolute,
regular, non-symlink file path. The stored bytes are re-hashed during replay,
so path indirection or file replacement cannot silently preserve qualification.

Portfolio-allocation qualification is artifact-bound. The preferred Phase 9
flow persists the exact multi-pool candidate corpus, source pool inputs,
account-state assumptions and ranked comparison as immutable
`PHASE9_PORTFOLIO_CANDIDATES_V1` evidence with a SHA-256. Qualified allocation
evidence must reference that evidence ID/hash for Phase 9 bundle readiness.
Free-form candidate files remain exploratory only.

Contextual-bandit qualification is likewise lineage-bound: qualified Phase 9
A persisted Phase 9 research promotion is considered current only when its stored promotion report still exactly matches the presently replay-verified, checksum-valid research bundle. The generic promotion row remains historical evidence; operational status must distinguish row existence from currentness. New component evidence, source tampering, replay mismatch or bundle refresh makes the previous Phase 9 promotion stale until it is revalidated and re-persisted. This currentness check remains research-only and grants no LIVE-policy authority.

bundle evidence must resolve its dataset evidence ID back to a persisted
`CONTINUOUS_RETRAIN_DATASET_V1` record and matching retraining cycle, dataset
version, SHA-256 and cutoff. Merely supplying lineage-shaped JSON is
insufficient.


Post-promotion Phase 9 shadow validation is a separate research-only boundary.
A shadow corpus must come from a newer checksum-bound retraining cycle whose
cutoff is after the persisted Phase 9 promotion. The future-policy
authorization evidence gate counts only unique deterministic-replay-verified
shadow cycles with independent dataset hashes/cutoffs and sufficient aggregate
validation depth. Both shadow and authorization evidence remain
`research_only=true`, `policy_actionable=false`; authorization evidence
also carries `execution_wired=false`. Persisted authorization evidence has a
currentness audit and is considered stale whenever current deterministic replay
no longer exactly matches it. No current LIVE executor or policy path consumes
Phase 9 authorization evidence.

A further controlled-validation boundary must use a fresh checksum-bound
holdout cycle after authorization evidence creation. The holdout cycle ID and
dataset SHA-256 must be independent of the corpora counted by authorization,
and deterministic replay must pass the controlled thresholds. Persisted
`PHASE9_POLICY_CONTROLLED_VALIDATION_V1` evidence is
`research_only=true`, `simulation_only=true`,
`policy_actionable=false` and `execution_wired=false`, with its own
currentness audit. Passing this boundary is validation evidence only and still
does not grant LIVE policy authority. A consolidated policy-readiness audit may report ready only when both the persisted authorization evidence and the fresh controlled holdout remain current under deterministic replay; that audit is also non-actionable and execution-disconnected. Any bounded rollout proposal must be evaluated only as disabled simulation evidence: its allowlist must be contained by the existing controlled-live allowlist, its numeric caps must not exceed existing limits, and the default canary requires a strictly narrower envelope with EXIT preserved and REBALANCE disabled. Persisted rollout simulation evidence cannot configure or authorize the executor. Rollback behavior is likewise defined first as non-actionable simulation evidence with explicit persisted criteria: stale rollout evidence and configured hard safety breaches resolve to a simulated rollback-required state, while performance floors are evaluated only after their configured sample depth. No rollback simulation may mutate positions, policy state or executor configuration. A consolidated pre-wiring audit may report ready only when the authorization/controlled-validation chain, the bounded rollout simulation and the rollback simulation are all current and the rollback state is `NO_ROLLBACK_TRIGGER`; even then, the audit remains research-only, simulation-only, policy-non-actionable and disconnected from execution. The dependency-aware Phase 9 work queue must preserve this ordering: future-policy evidence tasks are surfaced only after the persisted Phase 9 research promotion is current, and progress snapshots may report pre-wiring readiness only from current replay/audit state rather than historical row existence. `phase-status` must expose the same future-policy simulation currentness separately from Phase 9 research promotion state so historical promotion cannot be mistaken for policy authorization.
