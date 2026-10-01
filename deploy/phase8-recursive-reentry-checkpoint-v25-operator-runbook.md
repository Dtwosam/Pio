# Phase 8 checkpoint v25 continuation operator runbook

This runbook captures one checkpoint-v25 recursive re-entry continuation PAPER
evidence tick and its audit trail. It does not authorize recurring PAPER
collection, scheduler execution, live submission, new live capital, continuous
promotion, or Phase 8 promotion.

The machine-readable source of truth is:

`deploy/manifests/phase8-recursive-reentry-checkpoint-v25-continuation.json`

The manifest pins every reviewed tool by Git blob. Run the operator preflight
before creating or signing any new v25 artifact.

## Inputs

Set these paths explicitly in the operator shell:

```sh
REPO=/opt/pio
SOURCE_TREE=/path/to/reviewed/pio-source
ARTIFACT_DIR=/path/to/private/v25-artifacts
ALLOWED_SIGNERS=/path/to/allowed_signers
EXPECTED_ALLOWED_SIGNERS_SHA256=<sha256>
V24_EVIDENCE_BUNDLE=/path/to/sealed/v24-evidence-bundle.json
V24_POST_AUDIT=/path/to/sealed/v24-post-audit.json
SIGNATURE=/path/to/detached-signature
APPROVER_PRINCIPAL=<principal>
```

`ARTIFACT_DIR` must be operator-controlled. Saved JSON artifacts, the detached
signature, and the trust-root file must be regular files, not symlinks.

## 0. Read-only operator preflight

```sh
python "$SOURCE_TREE/deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_operator_preflight.py" \
  --repo "$REPO" \
  --source-tree "$SOURCE_TREE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$ARTIFACT_DIR/operator-preflight.json"
```

Continue only if the report has `preflight_ready=true`. The preflight itself
authorizes no PAPER tick.

## 1. Build the canonical v25 checkpoint

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_continuation_checkpoint_v25.py" \
  --source-tree "$SOURCE_TREE" \
  --evidence-bundle "$V24_EVIDENCE_BUNDLE" \
  --post-audit "$V24_POST_AUDIT" \
  > "$ARTIFACT_DIR/checkpoint.json"
```

The v25 checkpoint first verifies that the sealed v24 evidence bundle and its
matching v24 post-audit agree on receipt identity, pair/run scope, route, and
final DB/WAL/SHM state. Continue only if that provenance boundary passes and
the checkpoint route is `CONTINUE`. Terminal and recovery routes are separate
workflows.

## 2. Build fresh continuation readiness

```sh
python "$SOURCE_TREE/deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_continuation_supervision_readiness.py" \
  --repo "$REPO" \
  --source-tree "$SOURCE_TREE" \
  --checkpoint "$ARTIFACT_DIR/checkpoint.json" \
  > "$ARTIFACT_DIR/continuation-readiness.json"
```

Continue only if `readiness_status=READY`. A waiting state is a stop
condition, not permission to weaken freshness or quote-age bounds.

## 3. Build the non-authorizing tick request

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_request.py" \
  --source-tree "$SOURCE_TREE" \
  --continuation-readiness "$ARTIFACT_DIR/continuation-readiness.json" \
  > "$ARTIFACT_DIR/request.json"
```

The request does not authorize or execute the PAPER tick.

## 4. Build and externally sign the detached authorization

Build the payload:

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_signed_authorization.py" \
  build-payload \
  --source-tree "$SOURCE_TREE" \
  --request "$ARTIFACT_DIR/request.json" \
  --approver-principal "$APPROVER_PRINCIPAL" \
  > "$ARTIFACT_DIR/signed-payload.json"
```

Emit the exact bytes to the existing reviewed operator signing process:

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_signed_authorization.py" \
  emit-signing-bytes \
  --source-tree "$SOURCE_TREE" \
  --request "$ARTIFACT_DIR/request.json" \
  --payload "$ARTIFACT_DIR/signed-payload.json" \
  > "$ARTIFACT_DIR/signing-bytes"
```

The detached signature must cover those exact bytes under the namespace enforced
by the signer tool. Do not reconstruct, pretty-print, or edit the signing bytes.

Verify the detached signature and trust root:

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_signed_authorization.py" \
  verify \
  --source-tree "$SOURCE_TREE" \
  --request "$ARTIFACT_DIR/request.json" \
  --payload "$ARTIFACT_DIR/signed-payload.json" \
  --signature "$SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$ARTIFACT_DIR/signed-authorization-verification.json"
```

A failed, expired, not-yet-valid, or trust-root-mismatched authorization is a
hard stop.

## 5. Recheck execution readiness immediately before mutation

```sh
python "$SOURCE_TREE/deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_execution_readiness.py" \
  --repo "$REPO" \
  --source-tree "$SOURCE_TREE" \
  --checkpoint "$ARTIFACT_DIR/checkpoint.json" \
  --saved-continuation-readiness "$ARTIFACT_DIR/continuation-readiness.json" \
  --request "$ARTIFACT_DIR/request.json" \
  --saved-signed-verification "$ARTIFACT_DIR/signed-authorization-verification.json" \
  --signed-payload "$ARTIFACT_DIR/signed-payload.json" \
  --signature "$SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$ARTIFACT_DIR/execution-readiness.json"
```

Continue only if
`one_pair_evidence_tick_execution_readiness_ready=true`. Any DB, chain,
quote, run-id, pair-scope, source, request, or authorization drift is a hard
stop.

## 6. Execute exactly one PAPER tick

This is the only mutation-capable step in the manifest.

```sh
python "$SOURCE_TREE/deploy/tools/run_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_once.py" \
  --repo "$REPO" \
  --source-tree "$SOURCE_TREE" \
  --saved-execution-readiness "$ARTIFACT_DIR/execution-readiness.json" \
  --checkpoint "$ARTIFACT_DIR/checkpoint.json" \
  --saved-continuation-readiness "$ARTIFACT_DIR/continuation-readiness.json" \
  --request "$ARTIFACT_DIR/request.json" \
  --saved-signed-verification "$ARTIFACT_DIR/signed-authorization-verification.json" \
  --signed-payload "$ARTIFACT_DIR/signed-payload.json" \
  --signature "$SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$ARTIFACT_DIR/execution-receipt.json"
```

Do not loop this command. Do not retry it under the same authorization as a
substitute for a fresh reviewed request/readiness/authorization sequence. The
executor is bounded to one pair-scoped tick and emits a receipt that requires a
separate post-tick audit.

## 7. Audit the persisted result

```sh
python "$SOURCE_TREE/deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_post_audit.py" \
  --repo "$REPO" \
  --source-tree "$SOURCE_TREE" \
  --execution-receipt "$ARTIFACT_DIR/execution-receipt.json" \
  > "$ARTIFACT_DIR/post-audit.json"
```

The audit is read-only. Its route determines whether the pair is eligible for a
separate next-tick review, terminal evaluation review, or recovery review.

## 8. Seal the complete evidence bundle

Bundle immediately after the post-audit, before unrelated production DB changes
make the final-state binding stale.

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle.py" \
  --repo "$REPO" \
  --source-tree "$SOURCE_TREE" \
  --checkpoint "$ARTIFACT_DIR/checkpoint.json" \
  --continuation-readiness "$ARTIFACT_DIR/continuation-readiness.json" \
  --request "$ARTIFACT_DIR/request.json" \
  --signed-verification "$ARTIFACT_DIR/signed-authorization-verification.json" \
  --execution-readiness "$ARTIFACT_DIR/execution-readiness.json" \
  --execution-receipt "$ARTIFACT_DIR/execution-receipt.json" \
  --post-audit "$ARTIFACT_DIR/post-audit.json" \
  > "$ARTIFACT_DIR/evidence-bundle.json"
```

The bundle reruns every native artifact validator, checks cross-artifact
lineage, and requires the production DB/WAL/SHM to still match the final audit.
It authorizes no next action.

## Stop conditions

Stop the v25 sequence instead of weakening a gate when any of these occur:

- operator preflight is not ready;
- the sealed v24 evidence bundle and v24 post-audit do not match;
- checkpoint route is not `CONTINUE`;
- continuation readiness is not `READY`;
- a detached signature or trust-root check fails;
- authorization is expired or not yet valid;
- fresh execution readiness differs from saved readiness/request/authorization;
- DB, chain snapshot, quote map, pair scope, deterministic run identity, or
  reviewed source blob drifts;
- the executor reports a partial failure;
- either pair leg closes;
- the post-audit fails its ledger or persisted-run checks;
- the final database no longer matches the post-audit before bundling.

Recovery, terminal evaluation, another evidence tick, recurring PAPER
collection, live submission, new capital, and promotion all require separate
reviewed workflows and authorization.
