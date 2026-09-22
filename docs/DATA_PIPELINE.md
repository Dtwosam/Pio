# Data Pipeline

Status: Phase 1 implementation complete; extended live collection validation pending.

## Goal

Create reproducible API and on-chain time series before training any model.

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

The Rust executor has CI-validated read-only commands for:
- LbPair state
- active bin
- nearby BinArray state
- every bin in fetched arrays, including empty bins
- raw Q64 bin price
- bin X/Y inventory
- liquidity supply
- per-token fee checkpoints
- token mint/program IDs
- base/variable/total fee state
- deposit-time total fee after volatility decay/reference update
- protocol share and collect-fee mode
- exact DynamicPosition state
- per-bin position liquidity
- position token amounts
- pending fees and rewards

Rust emits JSON. Python stores these snapshots with its own observation timestamp.

## Chain replay

Repeated snapshots can be replayed as a small hypothetical standard-SPL LP.

The path:
1. selects the earliest observation in the requested recent window as entry;
2. distributes atomic X/Y using Meteora strategy rules;
3. mints source-backed hypothetical per-bin liquidity shares;
4. accounts for entry active-bin composition fees;
5. rechecks counterfactual share size at every observation;
6. attributes fee-checkpoint growth interval by interval;
7. marks ending inventory from final real per-share bin state.

Paths fail closed on missing coverage, zero historical supply, oversized counterfactual share, or unsupported token programs.

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
- `pio replay-chain`
- `pio scan-chain`
- `pio backtest-inventory`

## Remaining validation

- run extended live Data API collection
- run repeated high-frequency Solana snapshots for selected pools
- verify chain/API active-bin reconciliation
- build real-position replay/reconciliation samples
- measure snapshot gaps and counterfactual rejection rates
- export clean training frames
