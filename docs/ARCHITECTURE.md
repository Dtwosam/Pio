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
- mode
- data freshness
- capital limit
- pool allow/deny state
- current drawdown
- expected transaction cost
- transaction simulation

Only then may Rust sign in LIVE mode.

## Initial service interface

Proposal payload:

```json
{
  "decision_id": "uuid",
  "mode": "PAPER",
  "action": "ENTER",
  "pool_address": "...",
  "capital_quote": 100.0,
  "min_bin_id": 0,
  "max_bin_id": 0,
  "strategy": "SPOT",
  "expected_net_return": 0.0,
  "expected_downside": 0.0,
  "model_version": "baseline-v0"
}
```

Execution result:

```json
{
  "decision_id": "uuid",
  "accepted": false,
  "mode": "PAPER",
  "reason": "paper_mode",
  "signature": null
}
```
