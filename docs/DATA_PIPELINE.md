# Data Pipeline

Status: Phase 1 code complete; live collection validation pending.

## Goal

Create a reproducible time-series dataset before training any model.

## Collection order

1. Fetch protocol metrics.
2. Fetch paginated pool snapshots sorted by TVL.
3. Save the full raw JSON response before normalization.
4. Save normalized pool snapshots.
5. For configured top pools, fetch pool detail, OHLCV and volume history.
6. Normalize OHLCV and volume buckets into indexed tables.
7. Run OHLCV freshness, duplicate, gap and consistency checks.
8. Record endpoint-level errors without killing the full run.
9. Record collector run status as SUCCESS, PARTIAL or FAILED.

## Storage

SQLite is used first because it is embedded and deterministic.

Tables:
- raw_api_observations
- pool_snapshots
- ohlcv_candles
- volume_buckets
- data_quality_checks
- collection_errors
- collector_runs

Raw API payloads are retained so normalization logic can be changed later without losing the source observations.

## API safety

Meteora documents a 30 requests/second limit for DLMM APIs. Pio defaults to 20 requests/second and retries transport errors, HTTP 429 and HTTP 5xx responses with exponential backoff. Retry-After is honored when supplied.

## Data quality rule

Bad or stale data is not silently accepted. A pool can be collected while still being marked unsuitable for trading or model training.

Do not assume the upstream OHLCV endpoint provides unlimited history. Pio records actual observed coverage and will build its own continuous history through repeated collection.

## CLI

- pio collect-once
- pio protocol-metrics
- pio data-status

## Remaining Phase 1 validation

- run continuous collection against the live Meteora API
- inspect real response variations
- measure actual OHLCV depth/resolution
- verify gap thresholds against live data
- add incremental backfill where supported
- export training datasets to Parquet
