# Meteora Adaptive LP Bot — Build Phases

Status: v0.4

## Phase 0 — Foundation
Status: complete.

## Phase 1 — Market Data Engine
Status: implementation complete; extended live validation pending.

Built:
- current Meteora Data API timeseries contract
- raw API persistence
- normalized pool/OHLCV/volume data
- protocol-fee separation
- data-quality checks
- retries/rate limiting
- endpoint failure isolation

Exit condition:
- extended live collection remains healthy and reproducible.

## Phase 2 — Position Simulator / Backtester
Status: in progress.

Built:
- high-precision DLMM bin math
- Spot/Curve/Bid-Ask strategy weights
- candidate range grid
- discrete per-bin inventory simulator
- IL/hold/cost accounting
- inventory-only stored-data backtests
- PnL/history API normalization
- validation metrics
- pinned official Meteora Rust commons dependency
- read-only LbPair/BinArray inspection
- exact DynamicPosition inspection
- persistent chain pool/bin/position snapshots
- liquidity-concentration and fee-checkpoint activity features

Remaining:
- validate Rust build in a Rust-enabled environment
- extended chain snapshot collection
- exact hypothetical deposit liquidity-share creation
- active-bin partial-fill model
- fee attribution for hypothetical positions
- rebalance lifecycle simulation
- real-position reconciliation corpus

Exit condition:
- simulator error against real positions is measured and within an accepted tolerance.

## Phase 3 — Baseline Strategy
Status: scaffold exists; upgrade after Phase 2 validation.

Build:
- pool safety filters
- fee opportunity vs volatility/liquidity
- range and strategy choice
- position sizing
- exit/rebalance logic

## Phase 4 — ML v1

Walk-forward supervised models for:
- future net return
- downside
- fee yield
- range survival
- holding-period quality

## Phase 5 — Live Paper Trader

Run the full decision loop on live data without signing.

## Phase 6 — Rust Transaction Executor

Implement:
- wallet isolation
- create/add/remove liquidity
- claims
- transaction simulation
- priority fee handling
- confirmation/retry
- idempotency
- emergency close

## Phase 7 — Controlled Live Trading

Start with explicit, small capital caps.

## Phase 8 — Continuous Learning

Champion/challenger retraining, walk-forward validation, paper validation, promotion gates and rollback.

## Phase 9 — Advanced Edge

Potential additions:
- token risk
- wallet flow
- regime models
- adaptive ranges
- portfolio allocator
- perp hedging
- contextual bandits
