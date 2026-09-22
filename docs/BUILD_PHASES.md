# Meteora Adaptive LP Bot — Build Phases

Status: v0.3

## Phase 0 — Foundation
Status: complete.

## Phase 1 — Market Data Engine
Status: implementation complete; extended live validation pending.

Built:
- Meteora pool discovery and pool snapshots
- raw API persistence
- OHLCV and volume normalization
- freshness, duplicate, gap and OHLC consistency checks
- retry/rate-limit handling
- endpoint-level error isolation
- local data-status command

Exit condition:
- continuous historical/live dataset remains healthy through an extended collection run.

## Phase 2 — Position Simulator / Backtester
Status: in progress.

Built:
- verified bin-price math
- high-precision price/bin conversion
- current-price + active-bin anchoring
- exact strategy weight shapes for Spot/Curve/Bid-Ask
- candidate range grid including skewed ranges
- per-bin X/Y inventory state
- completed-bin conversion engine
- hold benchmark and IL
- explicit fees/cost accounting
- inventory-only stored-data backtest CLI
- Meteora position PnL/history API support
- PnL response normalization
- simulator validation metrics

Still required:
- real-position reconciliation corpus
- active-bin partial-fill model
- bin-liquidity snapshots
- position-attributable fee model
- rebalance lifecycle simulation

Exit condition:
- measured error against real positions is acceptable enough for model training.

## Phase 3 — Baseline Strategy
Status: scaffold exists; upgrade after simulator validation.

Build:
- bad-pool filters
- fee opportunity versus volatility
- range/strategy selection
- conservative position sizing
- exit-on-loss-of-edge logic

## Phase 4 — ML v1
Build supervised models for future net return, downside, fee yield, range survival and holding-period quality using walk-forward validation only.

## Phase 5 — Live Paper Trader
Run full decisions against live data with no signing and record every candidate, rejection, entry, rebalance, exit and expected-versus-realized result.

## Phase 6 — Rust Transaction Executor
Implement wallet isolation, Meteora account decoding, liquidity instructions, claims, transaction simulation, fee handling, confirmation/retry, idempotency and emergency close.

## Phase 7 — Controlled Live Trading
Start with strict capital caps. Capital increases only through explicit configuration.

## Phase 8 — Continuous Learning
Ingest outcomes, train challenger, walk-forward test, paper-test, compare against champion, promote only on passing gates and preserve rollback.

## Phase 9 — Advanced Edge
Potential additions include token safety, wallet flow, regime classifiers, adaptive ranges, cross-pool capital allocation, perp hedging and contextual bandits.
