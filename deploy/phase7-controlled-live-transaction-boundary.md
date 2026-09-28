# Phase 7 controlled-LIVE transaction readiness boundary

This runbook documents the reviewed Phase 7 controlled-LIVE evidence and transaction-readiness chain.

It does **not** authorize a transaction, load a private key, sign a message, call Solana
\`sendTransaction\`, widen controlled-LIVE limits, or persist Phase 7 promotion.

The terminal artifact produced by this chain is a **readiness-only** report. A later execution
component would still need its own separately reviewed implementation and must independently
enforce the exact transaction identity, authorization, blockhash liveness, one-submission scope,
confirmation reconciliation, and post-execution evidence handling.

## Reviewed tool blobs

The reviewed chain is:

- \`check_phase7_controlled_live_evidence_status.py\`
  - blob \`77aa894be5d3e04534e521d159fa4743e759ef06\`
- \`build_phase7_controlled_live_evidence_plan.py\`
  - blob \`3afb813bc08a57bc0a9d1a8d2df4439e4bfa8824\`
- \`check_phase7_controlled_live_input_preflight.py\`
  - blob \`3696ac1b747cedc6bd3e2c10c84c28ad81cfa711\`
- \`build_phase7_controlled_live_signed_authorization.py\`
  - blob \`c18c16dae3f65491ce4a660ec54f01dfe53a1cfa\`
- \`check_phase7_controlled_live_authorization_readiness.py\`
  - blob \`0df067b15e349736aee022c7404c5417c1c1b8b5\`
- \`check_phase7_controlled_live_presubmit_evidence.py\`
  - blob \`180980842a673354b024db78195b6bfe0209ab6f\`
- \`build_phase7_controlled_live_transaction_request.py\`
  - blob \`068173f916999bf0bef5c61dcf6cc996b8811804\`
- \`build_phase7_controlled_live_transaction_signed_authorization.py\`
  - blob \`4889d419f559503add46282fe40bb2d71ccd5ee0\`
- \`check_phase7_controlled_live_transaction_execution_readiness.py\`
  - blob \`61bb12102b2fb03593dc4bcb9d6130882a9809de\`

Any source change requires a new review and new blob pins. Do not silently substitute a newer
tool under an older artifact.

## 1. Read-only Phase 7 evidence status

The status checker evaluates the unchanged Phase 7 controlled-LIVE promotion criteria from a
private SQLite snapshot after confirmed Phase 6 promotion.

The reviewed defaults require:

- at least 3 CLOSED live positions;
- at least 2 distinct closed pools;
- at least 6 confirmed successful receipts;
- zero failed receipts;
- zero open/unsettled positions at validation;
- a clean live ledger;
- valuation coverage for every closed position;
- learning-label coverage for every closed position.

The source Pio database, WAL and SHM are hashed before and after evaluation. The evaluator fails
closed if the source database changes.

Even a ready status keeps all of these false:

- \`phase7_promotion_persisted\`
- \`phase7_promotion_authorized\`
- \`controlled_live_authorized\`
- \`live_submit_authorized\`
- \`transaction_signing_authorized\`
- \`transaction_submission_authorized\`
- \`live_capital_authorized\`

## 2. Advisory evidence plan

The evidence plan turns the status into a non-authorizing description of what evidence is still
missing.

It does not choose a pool, wallet, capital amount, budget, or live proposal.

New ENTER evidence is only a candidate when the existing corpus is already safe enough to take
additional risk:

- the live ledger is clean;
- there are no failed receipts;
- there are no open positions;
- every existing closed position is valued;
- every existing closed position is labeled; and
- Phase 7 still has a genuine evidence deficit.

Otherwise the plan routes to reconciliation, exit/closure, valuation, or labeling first.

The checked-in example controlled-LIVE config must remain disabled and placeholder-only. It is
not a production configuration.

## 3. Explicit-input preflight

The preflight accepts operator-owned inputs only:

- one production controlled-LIVE config;
- one exact LIVE ENTER proposal;
- one public executor-wallet identity.

It accepts no private key or secret signing material.

For the first evidence attempt the reviewed preflight narrows scope to:

- exactly one allowlisted pool;
- \`max_open_positions=1\`;
- exactly one daily ENTER submission;
- REBALANCE disabled;
- EXIT enabled;
- the per-entry capital cap equal to the exact proposal capital;
- the daily entry-capital cap equal to the exact proposal capital; and
- the realized-loss budget no greater than the exact proposal capital.

The preflight seals the config/proposal/wallet identity but still authorizes nothing.

## 4. Signed controlled-LIVE authorization

A detached SSH signature authenticates human intent for one exact preflight envelope.

The authorization binds the exact:

- input-preflight digest;
- evidence-plan and evidence-status digests;
- Phase 6 terminal-audit lineage;
- controlled-LIVE config digest;
- proposal digest;
- input-manifest digest; and
- public executor-wallet identity.

The allowed-signers file is an external trust root and its SHA-256 must be pinned by the caller.

This signature does not authorize transaction signing or submission. It requires a fresh
controlled-LIVE readiness recheck.

## 5. Fresh controlled-LIVE authorization readiness

The freshness gate reproduces and exactly matches:

- the Phase 7 evidence status;
- the advisory plan;
- the explicit-input preflight; and
- the signed controlled-LIVE authorization verification.

It also requires the current live corpus to remain safe for one new evidence entry: clean ledger,
zero failed receipts, zero open positions, and complete valuation/label coverage of existing
closed positions.

A ready result still has:

- \`transaction_signing_authorized=false\`
- \`transaction_submission_authorized=false\`
- \`live_capital_authorized=false\`

The next step is still a separate presubmit evidence gate.

## 6. Read-only presubmit evidence gate

The presubmit gate snapshots and rechecks the exact execution intent.

It requires the exact decision to remain:

- \`LIVE\`;
- \`ENTER\`;
- \`SIMULATION_PASSED\`;
- unsigned;
- error-free;
- risk accepted and proposal-bound;
- initial simulation successful;
- transaction guard accepted;
- restricted to the proposal pool;
- unsigned under the guard;
- exactly one required signature;
- fee payer bound to the authorized executor wallet;
- wallet authorization accepted and bound to that wallet;
- prepared transaction present and unsigned;
- recent blockhash present;
- final simulation successful; and
- exact intent snapshot unchanged.

The production execution database and sidecars are checked for mutation during snapshot/evaluation.

The gate records \`prepared_last_valid_block_height\` and explicitly requires another blockhash
expiry check immediately before execution.

It still does not sign or submit.

## 7. Non-authorizing transaction request

The transaction request binds the exact presubmit evidence into one narrow requested scope:

\`SIGN_AND_SUBMIT_EXACT_PHASE7_CONTROLLED_LIVE_TRANSACTION_ONLY\`

It explicitly excludes:

- a different decision ID;
- a different pool;
- a different executor wallet;
- a different proposal;
- a different controlled-LIVE config;
- a different prepared transaction;
- a different final simulation;
- REBALANCE;
- multiple submissions;
- unattended live operation;
- controlled-LIVE limit widening; and
- Phase 7 promotion.

The request requires a separate explicit human transaction authorization.

## 8. Signed transaction authorization

A second detached SSH authorization is specific to the exact transaction request.

It uses a dedicated transaction namespace and a deliberately short validity window:

- default TTL: 120 seconds;
- maximum TTL: 300 seconds.

The signed payload binds the exact prepared-transaction digest, final-simulation digest,
execution-intent snapshot, decision, pool, wallet, request, and last-valid block height.

The verification requires another final execution-readiness gate.

Even a valid signed transaction authorization still records:

- \`transaction_signing_authorized=false\`
- \`transaction_submission_authorized=false\`
- \`live_capital_authorized=false\`

## 9. Final read-only transaction execution readiness

The terminal readiness checker reproduces and exactly matches:

- the presubmit gate;
- the transaction request; and
- the transaction authorization verification.

It also requires:

- the transaction authorization is still unexpired;
- the execution intent is still unsigned;
- the execution intent is still error-free;
- the final simulation is still successful;
- an externally approved deployed executor-binary SHA-256;
- that exact executor binary contains the reviewed compiled \`live-submit\` runtime guard;
- \`PIO_LIVE_SUBMIT_ENABLED=1\` is present in the readiness environment;
- a read-only Solana \`getBlockHeight\` result at \`confirmed\` commitment; and
- \`current_block_height <= prepared_last_valid_block_height\`.

The RPC URL itself is not emitted; only its SHA-256 is recorded.

The executor-binary SHA-256 is an **external trust root**. This chain does not claim a
reproducible-build proof between source and binary merely because the hashes were supplied.

The readiness artifact requires immediate follow-on if an execution component is ever invoked,
because block height and authorization lifetime continue to move. The Rust submitter must still
perform its own native blockhash-expiry check.

## Stop boundary

Stop after the final readiness artifact.

A result with \`transaction_execution_readiness_ready=true\` means only that the reviewed
read-only evidence is fresh under the exact supplied inputs at that instant.

It does **not** mean:

- load an executor private key;
- sign the prepared transaction;
- submit the transaction;
- call Solana \`sendTransaction\`;
- retry or resubmit;
- widen controlled-LIVE limits;
- create unattended live automation;
- persist Phase 7 promotion; or
- authorize further live capital.

No tool in this reviewed chain is a transaction signer or submitter.

Any later execution component must be a separate review boundary and must, at minimum:

1. consume the exact final-readiness digest;
2. independently re-verify the exact signed transaction authorization;
3. independently re-read the execution intent and reject any drift;
4. independently recheck the current block height against the same prepared transaction;
5. use only the exact isolated executor wallet already bound by the evidence;
6. permit at most one submission for the exact decision;
7. preserve the Rust executor's persisted-signature/idempotent retry behavior;
8. record/reconcile confirmation before any new live risk; and
9. leave Phase 7 promotion as a separate post-evidence decision.

Do not treat a ready artifact, an authenticated request, the runtime environment variable, or the
existence of a feature-gated submit command as substitute authorization for execution.
