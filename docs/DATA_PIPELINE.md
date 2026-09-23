# Data Pipeline

Status: implemented; extended live collection and calibration pending.

## Data API

Pio stores:
- protocol/pool observations
- OHLCV
- volume + protocol fees
- exact raw responses
- position PnL
- position lifecycle history

Position history preserves transaction signature + instruction index so API lifecycle rows can be joined to decoded Solana events.

## On-chain Rust collection

Read-only commands collect:
- LbPair state
- every fetched bin, including empty bins
- Q64 price
- X/Y inventory and liquidity supply
- fee checkpoints
- effective reward checkpoints
- reward mints/rates/durations
- token program IDs
- deposit-time dynamic fee state
- exact DynamicPosition balances, shares, pending fees and rewards
- transaction event-CPI data
- transaction fee/compute-unit receipts
- requested add-liquidity bounds for common two-sided add variants

Commands:
- `inspect-pool`
- `inspect-position`
- `inspect-transaction-events`

## Replay/reconciliation

Chain replay:
1. chooses an entry observation;
2. allocates atomic X/Y using Meteora strategy rules;
3. mints hypothetical per-bin shares;
4. accounts for composition fees;
5. attributes fee/reward checkpoint growth;
6. marks ending inventory from real per-share bin state.

Rebalance replay chains these segments across observed range exits without silently reinvesting fees.

Real-position reconciliation compares Python amount/fee/reward math with DynamicPosition snapshots across consecutive eligible observations.

## Calibration flows

- `pio collect-position-history --position <POSITION>`
- decode matching transaction signatures with Rust
- `pio ingest-transaction-events`
- `pio composition-labels --position <POSITION>`
- `pio transaction-costs --position <POSITION>`
- `pio add-execution --position <POSITION>`

## Main tables

- raw_api_observations
- pool_snapshots
- ohlcv_candles
- volume_buckets
- chain_pool_snapshots
- bin_liquidity_snapshots
- chain_position_snapshots
- position_bin_snapshots
- position_event_history
- chain_transaction_snapshots
- chain_add_liquidity_requests
- chain_transaction_events
- data_quality_checks
- collection_errors
- collector_runs

## Data rule

Suspect, legacy or unsupported data is preserved but marked/ineligible. It is not silently promoted into training labels.
