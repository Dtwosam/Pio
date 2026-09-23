# Phase 7 Controlled Live Runbook

Status: controlled-live authorization, feature-gated submission, safety budgets and persistent Phase 7 validation workflow are implemented. Real Phase 6 promotion and controlled-live evidence are still pending; default builds cannot submit.

## Goal

Phase 7 starts controlled live trading only after persisted Phase 5 and Phase 6
promotion evidence plus current Phase 6 readiness are satisfied. The first live
path is intentionally narrow: one or very few allowlisted pools, tiny
quote-capital limits, hard concurrency/frequency/loss budgets, and a kill switch
that blocks new risk without blocking exits.

## Default state

The checked-in example is disabled:

```text
contracts/examples/controlled_live.example.json
```

It must not be treated as production-ready until its placeholder pool address is
replaced and the limits are reviewed for the actual account.

## Policy check

The controlled-live gate is read-only. It never signs or sends a transaction.

```bash
meteora-executor controlled-live-check \
  /absolute/path/to/pio.db \
  <PROPOSAL_JSON> \
  contracts/examples/controlled_live.example.json
```

For ENTER, approval requires:

- qualified persisted Phase 5 promotion evidence;
- `enabled=true`;
- proposal pool present in the explicit allowlist;
- non-zero finite proposal capital;
- proposal capital at or below the per-entry cap;
- daily submitted ENTER capital below its budget;
- daily ENTER count below its budget;
- effective open-position capacity after unresolved ENTER intents;
- no unvalued same-day CLOSED outcome;
- confirmed same-day realized loss below the independent loss budget;
- proposal daily drawdown at or below the configured threshold.

For REBALANCE, approval additionally requires `allow_rebalance=true`, the pool
to remain allowlisted, exactly one tracked active position for that pool, and a
rebalance count below the per-position churn cap. The same realized-loss and
valuation-completeness gates apply.

For EXIT, exactly one tracked active position is required, but the gate
intentionally does not require `enabled=true`, pool allowlisting, entry-capital
headroom, daily-entry headroom, or loss/drawdown headroom. `allow_exit=true`
keeps risk reduction available when new risk is disabled.

## Safety ordering

A future public live executor must not treat this policy check as a replacement
for Phase 6. The required order remains:

1. Phase 5 persisted promotion gate.
2. Persisted Phase 6 promotion gate.
3. Phase 6 readiness: isolated wallet + strict action-bound transaction policy.
4. Controlled-live authorization.
5. Existing Rust risk gate and persisted exact presign evidence.
6. Proposal/transaction/account/instruction binding.
7. Exact blockhash preparation and exact final simulation.
8. Wallet authorization.
9. Compile-time `live-submit` feature and runtime
   `PIO_LIVE_SUBMIT_ENABLED=1` opt-in.
10. Only then signing/submission.
11. Confirmation, receipt export, Python ingestion, ledger reconciliation and
    learning attribution.

## First controlled-live validation

The first live run must use the smallest practical capital and one allowlisted
pool. Success means the complete evidence graph reconciles, not merely that a
transaction lands.

Before increasing limits, require:

- confirmed Rust receipt;
- matching Solana transaction snapshot;
- applied live execution effect;
- correct live position lifecycle;
- position closure proof when closed;
- immutable atomic outcome;
- quote-valued outcome when required quotes exist;
- learning label reconciled to the original ENTER decision;
- clean `live-execution-ledger-audit --require-clean`.

No return target overrides these gates.


## Phase 7 persistent promotion

A landed transaction is not Phase 7 evidence by itself. Promotion requires a
closed and reconciled controlled-live corpus.

Evaluate:

```bash
pio phase7-validate --require-ready
```

Default criteria require:

- persisted `PHASE6_PROMOTION_V1`;
- at least 3 fully closed live positions;
- at least 2 distinct pools;
- at least 6 confirmed successful execution receipts;
- zero failed receipts;
- zero OPEN or LIQUIDITY_REMOVED positions at the validation snapshot;
- a clean `live-execution-ledger-audit`;
- every closed position has an immutable VALUED outcome;
- every closed position has a reconciled learning label.

Profitability is deliberately not a Phase 7 promotion criterion. The purpose of
this gate is to prove the controlled-live execution/evidence lifecycle works.

Persist only after the corpus passes:

```bash
pio phase7-validate --persist-ready --require-ready
meteora-executor phase7-promotion-gate /absolute/path/to/pio.db
```

Persisted `PHASE7_PROMOTION_V1` is the prerequisite for any future widening
of controlled-live limits or unattended live operation. Until then, the first
controlled-live configuration stays intentionally narrow.
