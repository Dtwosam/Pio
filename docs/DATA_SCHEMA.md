# Data Schema

## raw_api_observations

Append-only raw Meteora Data API observations.

## pool_snapshots

Normalized pool observations.

Important fields include:
- current_price
- bin_step
- derived active_bin_id when possible
- token symbols and decimals
- TVL
- 24h volume
- 24h fees
- APR/APY
- dynamic/base/max/protocol fee configuration
- blacklist state
- pool creation time

## ohlcv_candles

Deduplicated OHLCV data keyed by:
- pool_address
- source
- candle_time
- resolution/timeframe

## volume_buckets

Historical volume and fee buckets.

Pool fees and `protocol_fees` are stored separately.

## chain_pool_snapshots

Read-only Solana LbPair snapshots emitted by Rust.

Fields include:
- active_bin_id
- bin_step
- token mint addresses
- token program IDs
- base_fee_rate
- variable_fee_rate
- total_fee_rate
- deposit_total_fee_rate
- protocol_share_bps
- collect_fee_mode
- raw snapshot

Fee-rate integers are retained in Meteora's native 1e9 precision.

## bin_liquidity_snapshots

Per-bin on-chain state:
- raw Q64.64 price
- amount_x
- amount_y
- liquidity_supply
- fee_amount_x_per_token_stored
- fee_amount_y_per_token_stored

All large fixed-point/integer values are stored as text to avoid precision loss.

## chain_position_snapshots

Exact DynamicPosition aggregate state:
- pool and owner
- bin range
- token amounts
- pending fees
- rewards
- claimed fees
- last update timestamp

## position_bin_snapshots

Exact per-bin position state:
- position_liquidity
- pool bin liquidity
- position token amounts
- pending fee amounts
- rewards

This is the primary on-chain reconciliation source for position-state and fee math.

## Derived replay outputs

Chain replay results are currently computed on demand rather than persisted as source data.

They include:
- projected liquidity share per bin
- share bps versus observed real supply
- ending X/Y
- fee X/Y
- entry composition fee and protocol/LP split
- interval active-bin path
- range survival
- counterfactual fidelity/rejection state

## data_quality_checks

Current checks:
- non_empty
- duplicate_timestamps
- freshness
- time_gaps
- ohlc_consistency

## collection_errors

Endpoint-level failures tied to a collection run.

## collector_runs

Status:
- RUNNING
- SUCCESS
- PARTIAL
- FAILED
