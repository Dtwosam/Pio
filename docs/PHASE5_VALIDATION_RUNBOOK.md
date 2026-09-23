# Phase 5 PAPER Validation Runbook

Status: operational validation framework implemented; real endurance evidence still required.

## Purpose

Phase 5 is not complete merely because PAPER scheduling code exists.

This runbook defines the evidence needed to show that unattended PAPER operation can:
- keep running across many scheduler ticks;
- recover safely from abandoned scheduler leases;
- avoid duplicate chain valuation application;
- surface stale chain/quote dependencies;
- expose repeated failures before they become silent operational drift.

This is an execution-reliability gate. It is not a profitability guarantee.

## Evidence sources

Phase 5 validation reads persisted local evidence:

- `paper_ticks`: every idempotent orchestration tick and terminal status;
- `paper_scheduler_state`: current lease/failure state;
- `paper_scheduler_events`: lease acquisition, overlap, stale-lease recovery, and tick-finish history;
- `paper_chain_valuations`: applied chain-backed counterfactual valuations;
- `paper-health`: current scheduler/dependency health snapshot.

Restart and overlap behavior must be visible in persisted evidence rather than inferred from logs alone.

## Default endurance gate

`pio paper-endurance-report` currently uses these default engineering thresholds:

- at least 24 hours between first tick start and latest completed tick;
- at least 100 terminal ticks;
- at least 95% terminal ticks that are not `FAILED` or `MARKET_REFRESH_FAILED`;
- no more than 10% terminal ticks blocked on stale/missing chain or quote dependencies;
- no failure streak longer than 2 terminal ticks;
- no stale `RUNNING` tick older than 900 seconds;
- at least 24 applied chain valuations;
- at least 1 distinct paper position with an applied chain valuation.

These thresholds are intentionally configurable. Passing them is evidence of operational endurance, not sufficient evidence that the strategy has positive expected return.

## Commands

Current health:

```bash
pio paper-health --account paper --require-healthy
```

Independent event-ledger accounting audit:

```bash
pio paper-audit --account paper --require-passing
```

Prometheus-style health metrics:

```bash
pio paper-health --account paper --format prometheus
```

Scheduler state:

```bash
pio paper-scheduler-status --account paper
```

Endurance evidence:

```bash
pio paper-endurance-report --account paper
```

Fail the process unless the configured gate passes:

```bash
pio paper-endurance-report --account paper --require-passing
```

Example stricter multi-day gate:

```bash
pio paper-endurance-report \
  --account paper \
  --min-runtime-hours 72 \
  --min-terminal-ticks 500 \
  --min-success-rate-pct 99 \
  --max-dependency-blocked-pct 5 \
  --max-consecutive-failures 1 \
  --min-applied-chain-valuations 100 \
  --min-distinct-positions-valued 3 \
  --require-passing
```

## Controlled restart exercise

Use PAPER mode only.

1. Start the normal leased scheduler.
2. Confirm ticks are being persisted and `paper-health` is healthy or explains any dependency blocker.
3. Stop the scheduler process/service during a controlled test window.
4. Restart it after the previous lease has expired.
5. Confirm a `LEASE_RECOVERED` scheduler event was persisted.
6. Confirm the retried deterministic tick did not duplicate already-applied paper valuation/event keys.
7. Confirm the scheduler returns to a terminal status and the failure streak clears after a healthy tick.
8. Re-run the endurance report and retain the JSON evidence.

A live overlapping worker should produce `LEASE_BUSY` evidence and must not execute the tick concurrently.

## Phase 5 completion evidence

Before Phase 5 can be called complete, retain a real run corpus showing:

- the configured endurance gate passes;
- restart/lease recovery has been exercised without duplicate accounting;
- multiple real chain observations have produced applied valuations;
- dependency blockage is within the configured tolerance;
- no unexplained scheduler failure streak remains;
- current `paper-health` is acceptable at review time;
- `pio paper-audit --account paper --require-passing` passes, proving stored cash/position accounting reconciles to the immutable PAPER event ledger;
- paper PnL/accounting remains internally reconciled over the validation window.

External reward-token valuation is supported through fresh persisted ACCOUNT_QUOTE observations (including the optional Jupiter refresh path). Missing or stale reward quotes fail closed and block scheduling/valuation.
