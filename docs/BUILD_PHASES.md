# Meteora Adaptive LP Bot — Build Phases

Status: v0.1

## Phase 0 — Foundation

Deliverables:
- source of truth
- architecture
- repository structure
- shared config schema
- Rust executor skeleton
- Python learner skeleton
- paper/live mode separation

Exit condition:
- both services have clear interfaces and no private key is required for research mode.

## Phase 1 — Market Data Engine

Build:
- Meteora pool discovery
- pool snapshots
- OHLCV downloader
- volume-history downloader
- local persistence
- freshness checks
- retry/rate-limit handling

Store raw observations before feature transformation.

Exit condition:
- continuous historical/live dataset can be reproduced from saved raw data.

## Phase 2 — Position Simulator / Backtester

Build a DLMM-aware simulator for:
- selected bin ranges
- price movement through bins
- liquidity inventory changes
- fees
- estimated slippage
- transaction/rebalance costs
- exits and re-entries

Validation:
- compare simulated position results against real Meteora historical position/PnL data where available.

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

Decision score example:

`score = expected_net_return - lambda * expected_downside - expected_cost`

Exit condition:
- challenger beats deterministic baseline out of sample without materially worse drawdown.

## Phase 5 — Live Paper Trader

Run the full decision loop against live Meteora data without signing transactions.

Record:
- every considered candidate
- every rejected candidate and reason
- every hypothetical entry
- every rebalance
- every exit
- expected versus realized result

Exit condition:
- stable continuous operation and acceptable prediction calibration.

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

Initially use devnet or transaction simulation where practical, then tiny mainnet capital.

## Phase 7 — Controlled Live Trading

Start with a strict capital cap.

Promotion gates:
- paper-trading minimum sample reached
- positive cost-adjusted expectancy
- max drawdown below configured threshold
- no unresolved reconciliation errors
- transaction simulation success

Capital increases only through explicit config changes, never autonomously.

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

1. Scaffold services and config.
2. Implement Meteora Data API client.
3. Persist pool snapshots and candles.
4. Build candidate/action data model.
5. Build first deterministic scorer.
6. Build simulator.
7. Start collecting live paper-trading data.
8. Train ML v1 only after enough clean observations exist.
