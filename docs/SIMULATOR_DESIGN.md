# Simulator Design

Status: Phase 2 in progress.

## Non-negotiable rule

Simulator outputs are not treated as DLMM-accurate training labels until reconciled against real Meteora position outcomes.

## Current fidelity

`DISCRETE_COMPLETED_BIN_V1`

It models:
- Meteora bin-price ladder
- Spot / Curve / Bid-Ask weight shapes
- X inventory above active
- Y inventory below active
- completed-bin conversion
- mark-to-market inventory value
- hold benchmark
- IL
- explicit costs
- range survival
- externally supplied position-attributable fees

It intentionally does not infer:
- partial fill inside the current active bin
- exact intra-candle swap path
- hypothetical liquidity shares minted by a new deposit
- exact future fee attribution
- transaction slippage unless supplied

ZERO-fee backtests remain inventory/IL studies, not profitability claims.

## On-chain fidelity path

Rust can now read:
- exact active bin and nearby bin arrays
- bin amount_x / amount_y
- bin liquidity supply
- bin fee-per-token checkpoints
- exact existing DynamicPosition liquidity shares
- exact existing position amounts, pending fees and rewards

Python persists these snapshots for research and validation.

This removes **data access** as the main V2 blocker.

## Remaining V2 blocker

For a hypothetical new position, Pio still needs a validated way to determine the exact liquidity shares that Meteora would mint in each bin.

Until that is reproduced or obtained through safe transaction simulation:
- Pio may use real positions to validate fee math;
- Pio may use chain liquidity as market features;
- Pio must not present hypothetical fee estimates as exact.

## Validation sources

Meteora Data API:
- `GET /positions/{pool_address}/pnl?user=...`
- `GET /positions/{position_address}/historical`

On-chain Rust reader:
- `inspect-position`

Validation metrics:
- PnL MAE
- PnL RMSE
- PnL bias
- percentage error
- return error
- per-bin token-amount error
- per-bin position-liquidity error
- fee X/Y error

## Next fidelity step

Build a safe deposit simulation/quote path that returns the post-deposit position liquidity shares without sending a transaction. Then replay fee checkpoint growth against those shares.
