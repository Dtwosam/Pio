# Phase 3 Deterministic Policy

Status: implementation complete; promotion evidence pending

## Purpose

Phase 3 converts validated simulator output into transparent decisions before any ML model is allowed to influence capital.

The policy is deterministic and inspectable. It does not promise positive PnL and it does not increase risk to chase a return target.

## Entry pipeline

1. **Pool safety**
   - reject blacklisted or unknown-blacklist pools
   - require minimum TVL, 24h volume and pool age
   - enforce maximum configured dynamic fee
   - require enough local chain observations
   - require standard SPL token programs for the current high-fidelity path

2. **Range/strategy research**
   - replay Spot / Curve / Bid-Ask candidates
   - use only observations available before the decision point
   - compare range survival, cost-adjusted economics and counterfactual share
   - recenter the selected shape at the decision-time active bin

3. **Capital sizing**
   - preserve configured cash reserve
   - cap one-position exposure
   - cap total deployed capital
   - throttle size after soft drawdown
   - block new deployment at hard drawdown

4. **Entry authorization**
   - Phase 2 evidence must be promoted
   - pool safety must pass
   - a deterministic baseline candidate must pass
   - the scanned notional must fit the sizing cap
   - if sizing reduces the notional, the atomic proposal must be rescaled and replayed

## Existing-position policy

Priority order:
1. emergency exit
2. current pool safety failure
3. stop-loss
4. optional take-profit
5. maximum holding duration
6. out-of-range rebalance
7. rebalance-cap exit
8. optional proactive near-edge rebalance
9. hold

Safety exits override PnL. Pio must never keep an unsafe position open only because selling would realize a loss.

## Walk-forward rule

Candidate selection uses a trailing lookback window. The chosen shape is recentered at the decision observation and evaluated only on future observations. Forward evaluation windows cannot overlap.

Walk-forward validation is aggregated across pools with explicit sample, completion, positive-excess, average-edge and downside criteria. Phase 3 promotion is persisted only after Phase 2 is already persistently promoted and the configured Phase 3 criteria pass.

## Commands

```bash
pio screen-pools
pio baseline-walk-forward ...
pio size-position ...
pio manage-position ...
pio phase3-plan ...
```

## Promotion state

Cross-pool comparison uses equal requested notional and entry-value normalized basis-point economics. Promotion state is persisted and is the source of truth for downstream ML and paper gates.

Still required for promotion:
- real Phase 2 promotion evidence
- a sufficiently large multi-pool, regime-diverse walk-forward corpus
- passing configured Phase 3 validation thresholds

Live transaction construction/signing remains outside Phase 3.
