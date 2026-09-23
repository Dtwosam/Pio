# Phase 7 Controlled Live Runbook

Status: authorization infrastructure only. Public live signing/sending remains disabled.

## Goal

Phase 7 starts controlled live trading only after Phase 5 promotion evidence and
Phase 6 readiness are satisfied. The first live path is intentionally narrow:
one or very few allowlisted pools, a tiny quote-capital cap, a hard concurrency
limit, and a kill switch that blocks new risk without blocking exits.

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
- proposal capital at or below the hard entry cap;
- non-closed live positions below the configured concurrency cap;
- daily drawdown at or below the configured threshold.

For REBALANCE, approval additionally requires `allow_rebalance=true` and the
pool to remain allowlisted.

For EXIT, the gate intentionally does not require `enabled=true`, pool
allowlisting, capital headroom, or drawdown headroom. `allow_exit=true` is
sufficient after the Phase 5 dependency, so disabling new risk does not trap an
existing position.

## Safety ordering

A future public live executor must not treat this policy check as a replacement
for Phase 6. The required order remains:

1. Phase 5 persisted promotion gate.
2. Phase 6 readiness: isolated wallet + strict action-bound transaction policy.
3. Controlled-live authorization.
4. Existing Rust risk gate.
5. Proposal/transaction/account/instruction binding.
6. Simulation.
7. Exact blockhash preparation and exact final simulation.
8. Wallet authorization.
9. Only then signing/submission.
10. Confirmation, receipt export, Python ingestion, ledger reconciliation and
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
