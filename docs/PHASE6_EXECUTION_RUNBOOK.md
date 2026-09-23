# Phase 6 Rust Execution Runbook

Status: standard-SPL execution lifecycle implemented through unsigned construction, guarded presign, internal deterministic signing/submission, confirmation/receipt reconciliation, and live ledger mutation. Public live signing/sending remain disabled.

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
10. Fetch a fresh confirmed blockhash while the transaction is still unsigned.
11. Re-run transaction/account/instruction policy against that exact prepared message.
12. Re-authorize the isolated wallet against the exact prepared fee payer.
13. Simulate the exact prepared transaction with blockhash replacement disabled.
14. Persist the prepared transaction and exact final simulation.
15. Only then may the durable state machine enter `SIGNING`.

There is currently no public command that signs or sends a transaction. Internal signer/submission modules exist behind persisted final-presign evidence.

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

## Standard-SPL normal entry and rebalance

Normal entry uses a deterministic Meteora position PDA derived from the
executor wallet, pool, lower bin and width. This keeps the controlled-live path
to one isolated signer and avoids a second unmanaged position private key.

Chain-resolved entry:

```bash
meteora-executor build-standard-spl-entry-from-chain <ENTRY_REQUEST_JSON>
```

Before building it verifies pool ownership, standard-SPL token programs,
executor-owned token accounts and balances, position non-existence, touched
bin arrays and missing initialization accounts.

Normal rebalance consumes a precomputed remove/add plan. The executor does not
recompute strategy economics.

```bash
meteora-executor build-standard-spl-rebalance-from-chain <REBALANCE_REQUEST_JSON>
```

It verifies the executor owns the position, resolves the pool and standard-SPL
accounts from chain state, derives touched bin arrays and initializes missing
arrays. The controlled path is narrowed to auditable full-range repositioning
and fails closed outside its supported range/bitmap rules.

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

## Settlement and confirmed close

After liquidity has been fully removed, the standard-SPL settlement builder
claims fees, claims each active supported reward, and closes the position
account:

```bash
meteora-executor build-standard-spl-settlement-from-chain <SETTLEMENT_REQUEST_JSON>
```

The builder refuses non-zero position liquidity, wrong ownership, alternate
fee owners, missing reward destinations, unsupported Token-2022 reward/pool
mints and unsupported wide positions.

After the settlement receipt is confirmed, final account closure must be
proved independently:

```bash
meteora-executor verify-position-closed <RPC_URL> <POSITION_ADDRESS> > close-proof.json
pio finalize-live-position-closure \
  --decision <SETTLEMENT_DECISION_ID> \
  --file close-proof.json
```

The proof RPC slot must be at or after the confirmed settlement slot. A
position remains `LIQUIDITY_REMOVED` until this proof is accepted.

## Exact final presign

`execution-presign-prepare` refreshes the unsigned transaction to a current
confirmed blockhash, then re-runs the transaction guard and wallet
authorization against that exact message. The final RPC simulation uses the
same blockhash and does not replace it.

The durable execution journal stores:
- the exact unsigned prepared transaction;
- recent blockhash and last-valid block height;
- the final exact simulation result.

The store refuses `SIGNING` unless all of that evidence exists and passes.

## Internal signer and ambiguous submission recovery

The Rust library contains a deterministic single-signer implementation bound
to the exact persisted final-presign transaction. The fee payer must be the
isolated executor keypair and the prepared transaction must require exactly
one signer.

The internal submission coordinator persists `SENT` and the deterministic
signature before calling RPC. If RPC returns an ambiguous error, the intent
remains `SENT`. A retry may only regenerate and submit the same transaction
and signature.

Expired blockhashes are not blindly resubmitted. Use the read-only recovery
command for a persisted `SENT` intent:

```bash
meteora-executor execution-recovery \
  <EXECUTION_DB> <DECISION_ID> [EXPIRY_GRACE_BLOCKS]
```

Public sign/send commands remain deliberately absent.

## Execution receipts

A terminal Rust intent can be exported after confirmation/failure:

```bash
meteora-executor execution-receipt <EXECUTION_DB> <DECISION_ID> > receipt.json
```

The receipt binds:
- immutable decision ID;
- transaction signature;
- action and pool;
- terminal executor status;
- Solana slot and block time;
- network fee and compute usage when available;
- decoded event/add/rebalance counts.

Python can ingest it without any wallet access:

```bash
pio ingest-execution-receipt --file receipt.json
```

Receipt ingestion is idempotent. A decision ID cannot change receipts, and a
signature cannot be linked to two decisions. If the corresponding Solana
transaction snapshot is already present, slot/outcome/cost fields are checked
for consistency.

## Receipt-driven live ledger

After Rust receipt export and Solana transaction-event ingestion, Python can
derive immutable atomic execution effects:

```bash
pio apply-live-execution-effect --decision <DECISION_ID>
pio apply-live-position-effect --decision <DECISION_ID>
```

For a final settlement, use the closure proof flow above. A fully closed live
position can then produce immutable atomic outcome evidence:

```bash
pio build-live-position-outcome --position <POSITION_ADDRESS>
pio live-execution-ledger-audit --require-clean
```

The outcome includes exact token-X/token-Y wallet deltas, composition fees,
earned fees/rewards, and linked network fees. It is marked `ATOMIC_ONLY`;
quote-valued PnL and ML labels are not created until valuation evidence exists.

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

- Token-2022/transfer-hook execution construction and validation;
- quote-valued live PnL and final learning-label valuation from atomic outcomes;
- explicit operational validation of the full executor lifecycle after Phase 5 promotion.

Live signing/sending must not be enabled merely because the preflight layer
passes.


## Valued live outcomes and learning attribution

After a confirmed live lifecycle has been fully reconciled, Python keeps the
raw atomic evidence separate from valuation.

Build the immutable closed-position atomic outcome:

```bash
pio build-live-position-outcome --position <POSITION_ADDRESS>
```

Value that outcome using only persisted quotes observed at or before each
execution cashflow:

```bash
pio value-live-position-outcome   --position <POSITION_ADDRESS>   --max-age-seconds 300
```

The valuation fails closed if any counted execution effect is missing, pool
mint metadata is stale/missing, a non-zero reward has no reward mint, or a
required token/SOL quote is missing/stale. Principal cashflow, composition
costs, earned fees, rewards and network fees are persisted separately.

Export the original terminal Rust decision context from the execution journal:

```bash
meteora-executor execution-decision-context   <EXECUTION_DB> <OPENING_DECISION_ID> > decision-context.json
```

Ingest it into Python:

```bash
pio ingest-execution-decision-context --file decision-context.json
```

The context must be terminal (`CONFIRMED` or `FAILED`) and its action,
pool, signature and terminal status must reconcile to the execution receipt.

For a confirmed closed position, create the immutable learner label:

```bash
pio build-live-learning-label --position <POSITION_ADDRESS>
```

The label is accepted only when the originating context is a confirmed
`LIVE ENTER`, its signature/pool match the position, and its proposed range
matches the confirmed ENTER lifecycle event. It stores model version,
strategy, entry range, proposed capital, expected return/downside, realized
PnL/return and prediction error.

Audit the complete live evidence graph:

```bash
pio live-execution-ledger-audit --require-clean
```

A `VALUED` outcome without valuation evidence or without its learning label
makes the audit fail. Atomic-only outcomes are preserved until their required
historical quote evidence exists.
