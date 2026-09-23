# Meteora Adaptive LP Bot — Source of Truth

Status: v0.2
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
- no transaction when simulation fails
- no trade above configured capital-per-position limit
- no trade above configured portfolio exposure limit
- no automatic risk increase to chase a daily return target
- stop opening new positions after max daily drawdown is hit
- exit/reduce according to emergency rules when pool liquidity collapses or data becomes unreliable
- private keys never enter the ML service, logs, database or source code
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
