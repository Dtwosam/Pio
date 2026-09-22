# Data Pipeline

Status: Phase 1 in progress.

## Goal

Create a reproducible time-series dataset before training any model.

## Collection order

1. Fetch protocol metrics.
2. Fetch paginated pool snapshots sorted by TVL.
3. Save every raw API response unchanged.
4. Save a normalized pool snapshot for fast filtering.
5. For the configured top pools, save pool detail, OHLCV and volume-history responses.
6. Record collector success/failure and counts.

## Storage

The first implementation uses SQLite because it is embedded, deterministic and requires no external database service.

Tables:
- `raw_api_observations`
- `pool_snapshots`
- `collector_runs`

Raw payload storage is mandatory. Normalization may evolve as Meteora adds or changes fields without destroying source observations.

## API safety

Meteora documents a 30 requests/second limit for DLMM APIs. Pio defaults to 20 requests/second and retries HTTP 429/5xx responses with exponential backoff.

## Next additions

- normalized OHLCV candles
- normalized historical fee/volume buckets
- freshness and gap detection
- incremental backfill windows
- Parquet export for model training
