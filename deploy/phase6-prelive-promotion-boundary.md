# Phase 6 pre-live promotion boundary

This runbook describes the reviewed production continuation after a confirmed
Phase 5 promotion.

Its purpose is narrow: evaluate and, only after explicit short-lived human
authorization plus a fresh anti-replay recheck, persist the exact Phase 6
pre-live promotion evidence.

It does not authorize controlled LIVE execution, live-submit, transaction
signing/submission, or live capital. Those remain a separate Phase 7 workflow.

## Reviewed source blobs

The reviewed chain is pinned to these exact Git blobs:

- deploy/tools/check_phase6_prelive_evidence_status.py
  - 306497b937778cb8b77462f1457937f3499ca6ea
- deploy/tools/build_phase6_prelive_promotion_request.py
  - 214e18373a096444a8d710e7a4a40dcabfb8d811
- deploy/tools/build_phase6_prelive_promotion_signed_authorization.py
  - e8d9aa49cb2cc43ec80920a208493e6f4dee25ca
- deploy/tools/check_phase6_prelive_promotion_readiness.py
  - a347220a5e9a766de5477e09fc966fb94832cc96
- deploy/tools/persist_phase6_prelive_promotion.py
  - 06bb96bc54a7d639d779510231e19cea0bd402ca
- deploy/tools/check_phase6_prelive_post_promotion.py
  - a62643733bfab622cb8e59777cf31f4720ef6ce0

Use a separate reviewed source checkout. Do not pull, checkout, reset, restore,
clean, or otherwise run broad Git operations over /opt/pio.

## Required inputs

Before starting, identify:

- SRC: reviewed source checkout containing the exact blobs above;
- PHASE5_AUDIT: ready MANUAL_MARKET_PAPER_PHASE5_POST_PROMOTION_AUDIT_V1;
- EXECUTION_DB: absolute path to the Rust execution-intent SQLite database;
- ALLOWED_SIGNERS: externally maintained OpenSSH allowed_signers trust file;
- EXPECTED_ALLOWED_SIGNERS_SHA256: independently pinned trust-file SHA-256;
- APPROVER_PRINCIPAL: exact trusted signer principal.

The execution-database path is part of the evidence identity. Phase 6 status
preserves the canonical reviewed execution-database path inside its nested
report even though evaluation runs on a private snapshot.

## Phase 6 promotion criteria

The status tool uses the existing reviewed Phase 6 defaults unchanged:

- at least 10 valid LIVE ENTER intents with status SIMULATION_PASSED;
- at least 2 distinct validated pools;
- at least 2 blocked-path intents;
- exactly one authorized executor wallet across the validated passed corpus;
- complete accepted risk, transaction-guard, wallet-authorization, prepared
  unsigned transaction, and final simulation evidence for every passed intent;
- zero invalid passed intents;
- zero intents progressed beyond simulation into SIGNING, SENT, CONFIRMED, or
  FAILED;
- confirmed persisted Phase 5 promotion.

A ready corpus is still only pre-live evidence. It is not permission to submit
a transaction.

## 1. Build the read-only Phase 6 evidence status

~~~bash
PHASE6_STATUS=/var/tmp/pio-phase6-evidence-status.json

python3 \
  "$SRC/deploy/tools/check_phase6_prelive_evidence_status.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --phase5-post-promotion-audit "$PHASE5_AUDIT" \
  --execution-db "$EXECUTION_DB" \
  > "$PHASE6_STATUS"
~~~

The evaluator validates the Phase 5 audit, snapshots both source SQLite
databases, evaluates only private snapshots, seals exact criteria/report/corpus
lineage, and fails closed if source DB/WAL/SHM state changes.

Even a ready status requires:

- phase6_promotion_persisted=false
- phase6_promotion_authorized=false
- controlled_live_authorized=false
- live_submit_authorized=false
- transaction_signing_authorized=false
- transaction_submission_authorized=false
- live_capital_authorized=false

If phase6_promotion_ready=false, stop and collect the missing pre-live
simulation evidence. Do not bypass the reported reasons.

## 2. Build the non-authorizing promotion request

~~~bash
PHASE6_REQUEST=/var/tmp/pio-phase6-promotion-request.json

python3 \
  "$SRC/deploy/tools/build_phase6_prelive_promotion_request.py" \
  --source-tree "$SRC" \
  --phase6-evidence-status "$PHASE6_STATUS" \
  > "$PHASE6_REQUEST"
~~~

The request binds the exact status SHA-256, Phase 5 lineage, Pio/execution DB
digests, criteria/report digests, intent counts, pool diversity, blocked-path
evidence, zero post-simulation/invalid counts, and the one executor wallet.

It is material for a human decision, not an approval token. It still requires
explicit human authorization and a fresh Phase 6 promotion recheck.

## 3. Build and sign the short-lived authorization payload

~~~bash
PHASE6_PAYLOAD=/var/tmp/pio-phase6-promotion-authorization-payload.json

python3 \
  "$SRC/deploy/tools/build_phase6_prelive_promotion_signed_authorization.py" \
  build-payload \
  --source-tree "$SRC" \
  --request "$PHASE6_REQUEST" \
  --approver-principal "$APPROVER_PRINCIPAL" \
  > "$PHASE6_PAYLOAD"
~~~

The authorization lifetime is at most 15 minutes and defaults to 5 minutes.

Emit the exact canonical bytes:

~~~bash
PHASE6_SIGNING_BYTES=/var/tmp/pio-phase6-promotion-authorization.canonical

python3 \
  "$SRC/deploy/tools/build_phase6_prelive_promotion_signed_authorization.py" \
  emit-signing-bytes \
  --source-tree "$SRC" \
  --request "$PHASE6_REQUEST" \
  --payload "$PHASE6_PAYLOAD" \
  > "$PHASE6_SIGNING_BYTES"
~~~

Sign those bytes with the trusted Ed25519 SSH key under namespace
pio-phase6-prelive-promotion-authorization-v1. Keep the private approval key
outside the production repository.

Verify the signature:

~~~bash
PHASE6_VERIFICATION=/var/tmp/pio-phase6-promotion-authorization-verification.json

python3 \
  "$SRC/deploy/tools/build_phase6_prelive_promotion_signed_authorization.py" \
  verify \
  --source-tree "$SRC" \
  --request "$PHASE6_REQUEST" \
  --payload "$PHASE6_PAYLOAD" \
  --signature "$PHASE6_SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$PHASE6_VERIFICATION"
~~~

A valid verification authenticates only the exact Phase 6 promotion evidence.
It does not write promotion state and does not authorize controlled LIVE.

## 4. Run the final fresh promotion-readiness gate

The final read-only gate must run while the short-lived signature is valid:

~~~bash
PHASE6_READINESS=/var/tmp/pio-phase6-promotion-readiness.json

python3 \
  "$SRC/deploy/tools/check_phase6_prelive_promotion_readiness.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --phase5-post-promotion-audit "$PHASE5_AUDIT" \
  --saved-phase6-evidence-status "$PHASE6_STATUS" \
  --promotion-request "$PHASE6_REQUEST" \
  --saved-signed-authorization-verification "$PHASE6_VERIFICATION" \
  --signed-payload "$PHASE6_PAYLOAD" \
  --signature "$PHASE6_SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  --execution-db "$EXECUTION_DB" \
  > "$PHASE6_READINESS"
~~~

The readiness gate rebuilds the current status, re-verifies the signature,
requires exact equality with both saved artifacts, confirms Phase 5 is still
current, and requires no current Phase 6 row plus zero Phase 6 history rows.

That is the anti-replay boundary.

A ready result still has Phase 6 persistence and every controlled-LIVE,
signing/submission, and live-capital authorization flag false.

## 5. Persist the exact Phase 6 promotion evidence

This is the only production-write step in this runbook.

Read the exact readiness_sha256 from PHASE6_READINESS and pass it explicitly:

~~~bash
PHASE6_RECEIPT=/var/tmp/pio-phase6-promotion-persistence-receipt.json

python3 \
  "$SRC/deploy/tools/persist_phase6_prelive_promotion.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --phase5-post-promotion-audit "$PHASE5_AUDIT" \
  --saved-phase6-evidence-status "$PHASE6_STATUS" \
  --promotion-request "$PHASE6_REQUEST" \
  --saved-signed-authorization-verification "$PHASE6_VERIFICATION" \
  --signed-payload "$PHASE6_PAYLOAD" \
  --signature "$PHASE6_SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  --execution-db "$EXECUTION_DB" \
  --promotion-readiness "$PHASE6_READINESS" \
  --expected-promotion-readiness-sha256 "$PHASE6_READINESS_SHA256" \
  > "$PHASE6_RECEIPT"
~~~

The writer takes an exclusive runtime lock and BEGIN IMMEDIATE, re-runs the
complete readiness gate inside the lock, verifies table/immutable-history
contracts, rechecks Phase 5 and replay state, inserts exactly one Phase 6
history row and one current row, verifies both, and rolls back on any mismatch.

It does not modify the execution database.

A successful receipt confirms only the Phase 6 promotion write and still
requires a post-promotion audit.

## 6. Run the post-promotion audit

~~~bash
PHASE6_POST_AUDIT=/var/tmp/pio-phase6-post-promotion-audit.json

python3 \
  "$SRC/deploy/tools/check_phase6_prelive_post_promotion.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --phase5-post-promotion-audit "$PHASE5_AUDIT" \
  --promotion-request "$PHASE6_REQUEST" \
  --persistence-receipt "$PHASE6_RECEIPT" \
  --execution-db "$EXECUTION_DB" \
  > "$PHASE6_POST_AUDIT"
~~~

The audit is read-only. It requires exactly one Phase 6 history row and the
matching current row, confirms Phase 5 remains promoted, rebuilds Phase 6
evidence from snapshots, requires fresh report/criteria/execution-DB lineage to
match the persisted/requested evidence, and fails closed if either source
database changes during the audit.

A ready audit may report phase6_promotion_confirmed=true and
phase6_promotion_persisted=true. Those are confirmation facts, not execution
authorization.

## Stop boundary

Stop after a ready Phase 6 post-promotion audit.

The terminal audit must still have:

- requires_separate_phase7_workflow=true
- controlled_live_authorized=false
- live_submit_authorized=false
- transaction_signing_authorized=false
- transaction_submission_authorized=false
- live_capital_authorized=false

Phase 7 is a different risk boundary. Its evidence requires real controlled-live
lifecycle data and must have its own explicit authorization, capital/risk
envelope, fresh readiness checks, execution receipts, reconciliation, and stop
conditions.

Do not infer controlled-live permission from Phase 6 promotion.

Default builds remain unable to submit. The optional live-submit build and its
runtime controls remain separate additional gates.
