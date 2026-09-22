# Simulator Design

Status: Phase 2 in progress.

## Non-negotiable rule

Simulator outputs are not treated as DLMM-accurate training labels until they are reconciled against real Meteora position outcomes.

## Current fidelity

Current version:

DISCRETE_COMPLETED_BIN_V1

What it models:
- Meteora bin price ladder
- Spot / Curve / Bid-Ask strategy weights
- X inventory above the active bin
- Y inventory below the active bin
- completed-bin conversion as price crosses bins
- idle capital when the selected range cannot accept one side
- mark-to-market inventory value
- hold benchmark
- IL versus hold
- explicit transaction/rebalance/exit costs
- time in range
- externally supplied position-attributable fees

What it intentionally does not infer:
- partial fill inside the current active bin
- exact swap path within a candle
- per-bin pool liquidity share
- exact dynamic fee attribution to our position
- rewards unless supplied separately
- transaction slippage unless supplied as a cost

Therefore ZERO-fee backtests are inventory/IL studies, not profitability claims.

## Verified primitives

Meteora's current SDK computes raw bin price as:

price(bin_id) = (1 + bin_step / 10000) ^ bin_id

For real UI prices Pio can anchor to a known current price + active bin, then map price ratios to relative bin movement. This avoids decimal-offset errors when token decimals are not yet stored.

The current Meteora SDK strategy shapes are also mirrored:
- Spot: uniform bin weights
- Curve: weight toward the active area
- Bid-Ask: weight toward range edges

## Candidate grid

Pio generates combinations of:
- range half-width
- center offset / skew
- Spot
- Curve
- Bid-Ask

This lets future models learn which range/shape is appropriate for the market state rather than learning only which pool to select.

## Validation source

Meteora Data API provides:
- GET /positions/{pool_address}/pnl?user=...
- GET /positions/{position_address}/historical

The PnL response includes position bin range, deposits, withdrawals, fees, USD PnL and PnL percentage. Pio normalizes these fields for simulator reconciliation.

## Validation metrics

For matched real positions Pio measures:
- PnL MAE in USD
- PnL RMSE in USD
- PnL bias
- mean absolute error relative to actual PnL
- return error in percentage points

A simulator version must pass configured error tolerances across a meaningful sample before its outputs may become authoritative ML labels.

## Next fidelity step

DISCRETE_BIN_LIQUIDITY_V2 will require:
- active-bin history
- bin liquidity/distribution
- position share of each bin
- swap/fee flow by bin or reliable fee-growth accounting

That version can estimate position fees rather than receiving them exogenously.
