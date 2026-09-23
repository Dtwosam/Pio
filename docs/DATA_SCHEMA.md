# Data Schema

## Market data

### raw_api_observations
Append-only upstream payloads.

### pool_snapshots
Normalized pool state, token metadata, TVL, 24h volume/fees, APR/APY, active bin and fee configuration.

### ohlcv_candles
Deduplicated pool candles.

### volume_buckets
Historical volume, pool fees and protocol fees.

## Chain state

### chain_pool_snapshots
LbPair state:
- active_bin_id / bin_step
- token mint + program IDs
- base/variable/total/deposit-time fee rate
- protocol share / collect-fee mode
- limit-order support
- reward mint/rate/duration/update metadata

### bin_liquidity_snapshots
Per-bin:
- Q64 price
- X/Y
- liquidity supply
- fee checkpoint X/Y
- effective reward checkpoint slot 0/1

Large fixed-point integers are stored as text.

### chain_position_snapshots
DynamicPosition aggregate state:
- pool/owner/range
- token balances
- pending fees/rewards
- claimed fees
- reward campaign identity
- limit-order support

### position_bin_snapshots
Per-bin position state:
- position liquidity share
- pool bin liquidity
- position X/Y
- pending fees/rewards
- pool fee checkpoints
- effective reward checkpoints
- explicit reward-checkpoint availability flag

## Position lifecycle/API events

### position_event_history
Deduplicated Meteora Data API lifecycle rows keyed by position + signature + instruction index + event type.

Preserves exact amount strings and the signature/index join key.

## Transaction calibration

### chain_transaction_snapshots
One row per inspected transaction:
- slot/block time
- network_fee_lamports
- compute_units_consumed
- success status
- raw decoded snapshot

### chain_add_liquidity_requests
Decoded request bounds for supported two-sided add instructions:
- requested X/Y
- instruction type
- observed active id
- max active-bin slippage

### chain_transaction_events
Decoded event-CPI rows:
- AddLiquidity
- CompositionFee
- RemoveLiquidity
- Rebalancing

Typed lifecycle fields are stored alongside raw JSON.

## Derived outputs

Computed on demand:
- small-LP chain replay
- reward/fee attribution
- observation-boundary rebalance replay
- candidate scan
- exact reconciliation corpus
- Phase 2 promotion gate
- composition labels
- transaction-cost report
- add-execution calibration
