# Phase 6 Rust Execution Runbook

Status: guarded execution preflight and unsigned emergency-exit construction implemented. Live signing and transaction sending remain disabled.

## Purpose

Phase 6 owns the boundary between a strategy decision and a Solana transaction.

No strategy/model output is executable merely because it selected `LIVE`. The
Rust executor must independently prove that the proposal, transaction, wallet,
simulation and durable execution state all agree before a future signer is
allowed to act.

## Required execution order

The current fail-closed pipeline is:

1. Register one immutable execution intent by `decision_id`.
2. Run the Rust risk gate.
3. Bind the serialized transaction to:
   - the approved proposal pool;
   - the configured fee payer;
   - an explicit program allowlist;
   - an explicit proposal action;
   - allowed instruction-data prefixes/discriminators;
   - instruction/account-count limits;
   - unsigned-transaction policy;
   - address-lookup-table policy.
4. Persist the transaction-guard result.
5. Simulate the guarded transaction through Solana RPC.
6. Persist the simulation result.
7. Load the isolated executor wallet from `PIO_EXECUTOR_KEYPAIR`.
8. Verify the wallet public key matches the guarded transaction fee payer.
9. Persist wallet authorization.
10. Only then may the durable state machine enter `SIGNING`.

There is currently no public command that signs or sends a transaction.

## Wallet isolation

`PIO_EXECUTOR_KEYPAIR` must point to an absolute regular file.

The Rust wallet loader rejects:
- relative paths;
- symlinks;
- group/world-readable keypair files on Unix;
- inline private-key material.

`wallet-status` returns only public wallet metadata.

## Transaction guard

A transaction policy is part of the immutable execution intent. Reusing a
`decision_id` with a different request, risk config or transaction policy
fails closed.

Instruction policy can bind an on-chain program plus one or more instruction
data prefixes to specific proposal actions.

For the pinned Meteora DLMM SDK revision, `RemoveAllLiquidity` uses the
8-byte discriminator:

```text
0a333d2370691855
```

The checked-in emergency-exit policy example allows that discriminator only
for `EXIT`.

## Guarded dry run

The durable dry-run command requires both risk and transaction policy:

```bash
meteora-executor dry-run-execution \
  <REQUEST_JSON> \
  contracts/examples/risk_config.example.json \
  <TRANSACTION_GUARD_CONFIG_JSON> \
  <EXECUTION_DB>
```

On restart:
- completed risk/transaction/simulation results are reused;
- an RPC failure after transaction validation resumes from the persisted
  transaction guard;
- a rejected transaction never reaches simulation.

The stateless equivalent is:

```bash
meteora-executor preflight-execution \
  <REQUEST_JSON> \
  contracts/examples/risk_config.example.json \
  <TRANSACTION_GUARD_CONFIG_JSON>
```

## Wallet authorization

After a durable simulation has passed:

```bash
meteora-executor execution-wallet-authorize \
  <EXECUTION_DB> \
  <DECISION_ID>
```

The execution store refuses `SIGNING` state without persisted accepted wallet
authorization.

## Emergency exit

The first Phase 6 instruction builder removes all standard-SPL DLMM liquidity.
It does not claim fees/rewards and does not close the position account.

The public builder resolves sensitive accounts from chain state. The caller
supplies only:
- position address;
- executor-owned token-X destination account;
- executor-owned token-Y destination account;
- executor wallet public key.

Example request:

```text
contracts/examples/emergency_exit.chain.example.json
```

Build:

```bash
meteora-executor build-emergency-exit \
  contracts/examples/emergency_exit.chain.example.json
```

Before producing the unsigned transaction, the builder verifies:
- the position and pool are owned by the Meteora DLMM program;
- the executor wallet owns the position;
- the pool uses standard SPL Token for X and Y;
- destination token accounts have the correct mints and owner;
- reserves and mints exist under the standard token program;
- required bin arrays are real Meteora accounts;
- the bitmap extension is included when it exists.

The output is still unsigned. It should then pass the strict transaction guard
and simulation pipeline. Token-2022 emergency exits remain fail-closed until
their transfer-hook/remaining-account path is implemented and validated.

## Confirmation recovery

For an already persisted `SENT` intent:

```bash
meteora-executor execution-confirmation <EXECUTION_DB> <DECISION_ID>
```

The command performs read-only signature-status lookup with transaction-history
search enabled.

It is idempotent:
- pending leaves the intent at `SENT`;
- confirmed transitions once to `CONFIRMED`;
- chain failure transitions once to `FAILED`;
- already terminal intents are returned without another RPC observation.

This does not resend the transaction.

## Still required before live execution

Phase 6 is not complete yet. Remaining work includes:

- instruction builders for normal entry and rebalance;
- fee/reward claim and final position-close transaction sequence;
- Token-2022/transfer-hook execution construction and validation;
- exact recent-blockhash preparation and final pre-sign simulation;
- isolated signer implementation;
- transaction send/retry logic that cannot duplicate execution;
- controlled recovery for expired blockhashes and ambiguous send outcomes;
- live execution receipts reconciled back into the Python accounting layer;
- explicit operational validation of the full executor lifecycle.

Live signing/sending must not be enabled merely because the preflight layer
passes.
