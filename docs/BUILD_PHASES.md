# Meteora Adaptive LP Bot — Build Phases

Status: v0.2

## Phase 0 — Foundation

Status: complete.

Deliverables:
- source of truth
- architecture
- repository structure
- shared config schema
- Rust executor skeleton
- Python learner skeleton
- paper/live mode separation

## Phase 1 — Market Data Engine

Status: implementation complete; live validation pending.

Built:
- Meteora pool discovery
- pool snapshots
- OHLCV downloader
- volume-history downloader
- raw-response persistence
- normalized candle/volume tables
- freshness, duplicate, gap and OHLC consistency checks
- retry/rate-limit handling
- endpoint-level error isolation
- local data-status command

Exit condition:
- continuous historical/live dataset can be reproduced from saved raw data and remains healthy during an extended live collection run.

## Phase 2 — Position Simulator / Backtester

Status: next.

Build a DLMM-aware simulator for:
- selected bin ranges
- price movement through bins
- liquidity inventory changes
- fees
- estimated slippage
- transaction/rebalance costs
- exits and re-entries

Validation:
- compare simulated position results against real Meteora position/PnL data where available.

Exit condition:
- simulation error is measured and acceptable enough for model training.

## Phase 3 — Baseline Strategy

Create a non-ML benchmark that:
- filters bad pools
- scores fee opportunity versus volatility
- chooses candidate ranges
- sizes positions conservatively
- exits on loss of edge

Purpose:
- create a strategy the ML system must beat.

## Phase 4 — ML v1

Build supervised models for:
- future net return
- downside risk
- fee yield
- range survival probability
- holding-period quality

Use walk-forward validation only. No random train/test split across time.

Decision score:

score = expected_net_return - lambda * expected_downside - expected_cost

Exit condition:
- challenger beats deterministic baseline out of sample without materially worse drawdown.

## Phase 5 — Live Paper Trader

Run the full decision loop against live Meteora data without signing transactions.

Record every considered candidate, rejection, hypothetical entry, rebalance, exit, and expected-versus-realized result.

## Phase 6 — Rust Transaction Executor

Implement:
- wallet isolation
- Meteora account decoding
- create/add/remove liquidity instructions
- claims
- transaction simulation
- priority fee handling
- confirmation/retry logic
- idempotency
- emergency close

Initially use transaction simulation/devnet where practical, then tiny mainnet capital.

## Phase 7 — Controlled Live Trading

Start with a strict capital cap.

Promotion gates:
- paper-trading minimum sample reached
- positive cost-adjusted expectancy
- max drawdown below configured threshold
- no unresolved reconciliation errors
- transaction simulation success

Capital increases only through explicit config changes.

## Phase 8 — Continuous Learning

Pipeline:
1. ingest completed outcomes
2. validate data
3. retrain challenger
4. walk-forward test
5. paper-test challenger
6. compare with champion
7. promote only if promotion rules pass
8. keep rollback copy of champion

## Phase 9 — Advanced Edge

Potential additions:
- token safety model
- wallet-flow features
- new-pool regime classifier
- market-regime classifier
- adaptive range width
- adaptive rebalance thresholds
- cross-pool capital allocator
- perp hedge engine
- contextual bandit for action selection

## Immediate build order

1. Validate Phase 1 against live API.
2. Build dataset export and feature generation.
3. Build DLMM bin/range simulator.
4. Validate simulator against observed position economics.
5. Build deterministic baseline.
6. Start live paper trading.
7. Train ML v1 only after enough clean observations exist.
