# Meteora Adaptive LP Bot — Build Phases

Status: v0.5

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
Status: advanced implementation; reconciliation still pending.

Built:
- high-precision DLMM bin math
- SDK-equivalent Spot/Curve/Bid-Ask deposit allocation
- source-backed empty/existing-bin liquidity-share formulas
- withdrawal/pro-rata amount math
- discrete OHLC inventory simulator
- IL/hold/cost accounting
- PnL/history API normalization
- simulator validation metrics
- pinned official Meteora Rust commons dependency
- CI-validated read-only LbPair/BinArray inspection
- exact DynamicPosition inspection
- persistent chain pool/bin/position snapshots
- raw Q64 bin prices including empty bins
- token program and on-chain fee-state capture
- deposit-time dynamic fee calculation from Solana clock
- composition-fee accounting
- multi-snapshot small-LP counterfactual replay
- interval-by-interval fee dilution
- fail-closed Token-2022 gate
- fail-closed counterfactual-size/zero-supply guards
- chain-backed candidate scanner

Remaining:
- extended chain snapshot collection
- real-position reconciliation corpus
- active-bin composition reconciliation against real adds
- Token-2022 transfer-fee mint-extension support
- hypothetical reward accounting
- rebalance lifecycle simulation
- transaction/slippage cost calibration

Exit condition:
- simulator error against real positions is measured and within accepted tolerance.

## Phase 3 — Deterministic Baseline Strategy
Status: chain-backed candidate comparison exists; decision policy not yet promoted.

Build:
- pool safety filters
- comparable value/PnL normalization
- fee opportunity vs volatility/liquidity
- deterministic range/strategy policy
- conservative position sizing
- exit/rebalance policy
- walk-forward baseline evaluation

The baseline must consume validated Phase 2 outputs. It must not rank candidates using fake APY or pool-level fees as if they belonged to the position.

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
