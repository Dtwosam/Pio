# Data Pipeline

Status: Phase 1 implementation complete; extended live collection validation pending.

## Goal

Create a reproducible time-series dataset before training any model.

## Data API collection

1. Fetch protocol metrics.
2. Fetch paginated pool snapshots sorted by TVL.
3. Save raw JSON before normalization.
4. Save normalized pool snapshots.
5. Fetch pool detail for configured pools.
6. Fetch OHLCV and volume history with Meteora's current `timeframe`, `start_time`, and `end_time` parameters.
7. Normalize candles and volume buckets.
8. Preserve pool fees and protocol fees separately.
9. Run freshness, duplicate, gap and OHLC consistency checks.
10. Record endpoint failures without killing the entire run.

Default history settings:
- timeframe: 5m
- lookback: 24 hours
- stale threshold: 15 minutes

Repeated runs build our own continuous history.

## On-chain collection

The Rust executor has read-only commands for:
- LbPair state
- active bin
- nearby BinArray state
- bin X/Y inventory
- liquidity supply
- per-token fee checkpoints
- exact DynamicPosition state
- per-bin position liquidity
- position token amounts
- pending fees and rewards

Rust emits JSON. Python stores these snapshots with its own observation timestamp.

## Main tables

- raw_api_observations
- pool_snapshots
- ohlcv_candles
- volume_buckets
- chain_pool_snapshots
- bin_liquidity_snapshots
- chain_position_snapshots
- position_bin_snapshots
- data_quality_checks
- collection_errors
- collector_runs

## API safety

Pio stays below Meteora's documented DLMM API rate limit by default and retries transport errors, HTTP 429 and HTTP 5xx responses with backoff. `Retry-After` is honored when supplied.

## Data quality rule

Suspect data is stored and marked; it is not silently promoted into model-training data.

## CLI

- `pio collect-once`
- `pio protocol-metrics`
- `pio data-status`
- `pio ingest-chain-snapshot`
- `pio ingest-position-snapshot`
- `pio backtest-inventory`

## Remaining validation

- run extended live Data API collection
- run repeated Solana chain snapshots for selected pools
- verify chain/API active-bin reconciliation
- measure historical coverage and gaps
- export clean training frames
