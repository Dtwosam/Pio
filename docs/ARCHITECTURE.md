# Architecture

```text
                +----------------------+
                | Meteora Data API     |
                +----------+-----------+
                           |
                           v
+----------------+  +------+-------+     +-------------------+
| Solana RPC     |->| Data Store   |<----| Feature Pipeline  |
+-------+--------+  +------+-------+     +---------+---------+
        |                  ^                       |
        |                  |                       v
        |          trade outcomes          +-------+--------+
        |                  |               | ML / Baseline  |
        |                  |               | Decision Engine|
        |                  |               +-------+--------+
        |                  |                       |
        |                  |                 proposals only
        |                  |                       v
        |          +-------+-----------------------+--------+
        +--------->| Rust Risk + Execution Engine          |
                   +----------------+-----------------------+
                                    |
                              signed tx only
                                    v
                              Meteora DLMM
```

## Boundary rule

Python never owns the signing key.

The learner emits a proposal. Rust independently validates:
- execution mode
- data freshness
- capital as a percentage of account equity
- projected portfolio deployment
- current daily drawdown
- expected edge/downside
- pool allow/deny state
- transaction cost and simulation once execution is implemented

Only then may Rust sign in LIVE mode.

EXIT actions remain available when entry gates such as stale-data or drawdown limits are active. This prevents a safety rule from trapping capital in a position.

## Proposal contract

Percentage fields use percentage units: 0.25 means 0.25%.

```json
{
  "decision_id": "uuid",
  "mode": "PAPER",
  "action": "ENTER",
  "pool_address": "...",
  "capital_quote": 20.0,
  "account_equity_quote": 1000.0,
  "portfolio_deployed_quote": 100.0,
  "daily_drawdown_pct": 0.5,
  "min_bin_id": 0,
  "max_bin_id": 0,
  "strategy": "SPOT",
  "expected_net_return_pct": 1.0,
  "expected_downside_pct": 0.5,
  "model_version": "baseline-v0",
  "data_age_seconds": 5
}
```

## Execution result

```json
{
  "decision_id": "uuid",
  "accepted": false,
  "mode": "PAPER",
  "reason": "non_live_mode",
  "signature": null
}
```
