# Data Schema

## raw_api_observations

Append-only source observations from Meteora.

Key fields:
- observed_at
- endpoint
- entity_key
- payload_json

## pool_snapshots

Normalized pool-level observations used for scanning.

Key fields:
- observed_at
- address
- name
- tvl
- volume_24h
- fees_24h
- raw_json

## ohlcv_candles

Deduplicated normalized candles.

Natural key:
- pool_address
- source
- candle_time
- resolution

Fields include open, high, low, close, volume and observed_at.

## volume_buckets

Deduplicated historical volume/fee buckets.

Natural key:
- pool_address
- source
- bucket_time

## data_quality_checks

Stores quality outcomes rather than silently dropping suspect data.

Current checks:
- non_empty
- duplicate_timestamps
- freshness
- time_gaps
- ohlc_consistency

Status values:
- PASS
- FAIL

## collection_errors

Endpoint-level failures tied to a collector run. One pool failing history collection does not invalidate every other pool in the run.

## collector_runs

One row per collector execution.

Status:
- RUNNING
- SUCCESS
- PARTIAL
- FAILED
