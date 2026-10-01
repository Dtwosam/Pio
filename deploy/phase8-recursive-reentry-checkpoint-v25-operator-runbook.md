# Phase 8 checkpoint v25 continuation operator runbook

This runbook captures one checkpoint-v25 recursive re-entry continuation PAPER
evidence tick and its audit trail. It does not authorize recurring PAPER
collection, scheduler execution, live submission, new live capital, continuous
promotion, or Phase 8 promotion.

The machine-readable operator-surface source of truth is:

`deploy/manifests/phase8-recursive-reentry-checkpoint-v25-operator-surface.json`

It pins the read-only operator preflight, saved-artifact status verifier,
evidence-handoff verifier, this runbook, and the core execution manifest.

The core eight-stage execution/evidence sequence remains defined by:

`deploy/manifests/phase8-recursive-reentry-checkpoint-v25-continuation.json`

Before using any v25 operator workflow, verify the reviewed source surface:

```sh
python "$SOURCE_TREE/deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_operator_surface.py" \
  --source-tree "$SOURCE_TREE" \
  > "$ARTIFACT_DIR/operator-surface-integrity-v25.json"
```

Continue only if the report has `support_surface_verified=true` and
`core_execution_manifest_verified=true`. The surface check is read-only,
is not an execution sequence, and authorizes no next action. Run the operator
preflight before creating or signing any new v25 artifact.

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
  > "$ARTIFACT_DIR/operator-preflight-v25.json"
```

Continue only if the report has `preflight_ready=true`. The preflight itself
authorizes no PAPER tick.

## 0a. Inspect saved artifact status

At any point, inspect the operator directory without creating, signing, or
executing anything:

```sh
python "$SOURCE_TREE/deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_artifact_status.py" \
  --source-tree "$SOURCE_TREE" \
  --artifact-dir "$ARTIFACT_DIR" \
  > "$ARTIFACT_DIR/artifact-status-v25.json"
```

The status report verifies the pinned manifest/tool blobs and applies each
stage's native validator to artifacts already present under their canonical
manifest filenames. It reports missing, invalid, unsafe, or out-of-order
artifacts and classifies the next boundary. A
`MUTATION_BOUNDARY_REVIEW_REQUIRED` result is not authorization to execute.
The status tool does not reverify the detached SSH signature and does not
revalidate the production database; run the fresh preflight and normal reviewed
step immediately before any operator sequence.

## 1. Build the canonical v25 checkpoint

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_continuation_checkpoint_v25.py" \
  --source-tree "$SOURCE_TREE" \
  --evidence-bundle "$V24_EVIDENCE_BUNDLE" \
  --post-audit "$V24_POST_AUDIT" \
  > "$ARTIFACT_DIR/checkpoint-v25.json"
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
  --checkpoint "$ARTIFACT_DIR/checkpoint-v25.json" \
  > "$ARTIFACT_DIR/continuation-readiness-v25.json"
```

Continue only if `readiness_status=READY`. A waiting state is a stop
condition, not permission to weaken freshness or quote-age bounds.

## 3. Build the non-authorizing tick request

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_request.py" \
  --source-tree "$SOURCE_TREE" \
  --continuation-readiness "$ARTIFACT_DIR/continuation-readiness-v25.json" \
  > "$ARTIFACT_DIR/request-v25.json"
```

The request does not authorize or execute the PAPER tick.

## 4. Build and externally sign the detached authorization

Build the payload:

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_signed_authorization.py" \
  build-payload \
  --source-tree "$SOURCE_TREE" \
  --request "$ARTIFACT_DIR/request-v25.json" \
  --approver-principal "$APPROVER_PRINCIPAL" \
  > "$ARTIFACT_DIR/signed-payload-v25.json"
```

Emit the exact bytes to the existing reviewed operator signing process:

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_signed_authorization.py" \
  emit-signing-bytes \
  --source-tree "$SOURCE_TREE" \
  --request "$ARTIFACT_DIR/request-v25.json" \
  --payload "$ARTIFACT_DIR/signed-payload-v25.json" \
  > "$ARTIFACT_DIR/signing-bytes-v25"
```

The detached signature must cover those exact bytes under the namespace enforced
by the signer tool. Do not reconstruct, pretty-print, or edit the signing bytes.

Verify the detached signature and trust root:

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_signed_authorization.py" \
  verify \
  --source-tree "$SOURCE_TREE" \
  --request "$ARTIFACT_DIR/request-v25.json" \
  --payload "$ARTIFACT_DIR/signed-payload-v25.json" \
  --signature "$SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$ARTIFACT_DIR/signed-authorization-verification-v25.json"
```

A failed, expired, not-yet-valid, or trust-root-mismatched authorization is a
hard stop.

## 5. Recheck execution readiness immediately before mutation

```sh
python "$SOURCE_TREE/deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_execution_readiness.py" \
  --repo "$REPO" \
  --source-tree "$SOURCE_TREE" \
  --checkpoint "$ARTIFACT_DIR/checkpoint-v25.json" \
  --saved-continuation-readiness "$ARTIFACT_DIR/continuation-readiness-v25.json" \
  --request "$ARTIFACT_DIR/request-v25.json" \
  --saved-signed-verification "$ARTIFACT_DIR/signed-authorization-verification-v25.json" \
  --signed-payload "$ARTIFACT_DIR/signed-payload-v25.json" \
  --signature "$SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$ARTIFACT_DIR/execution-readiness-v25.json"
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
  --saved-execution-readiness "$ARTIFACT_DIR/execution-readiness-v25.json" \
  --checkpoint "$ARTIFACT_DIR/checkpoint-v25.json" \
  --saved-continuation-readiness "$ARTIFACT_DIR/continuation-readiness-v25.json" \
  --request "$ARTIFACT_DIR/request-v25.json" \
  --saved-signed-verification "$ARTIFACT_DIR/signed-authorization-verification-v25.json" \
  --signed-payload "$ARTIFACT_DIR/signed-payload-v25.json" \
  --signature "$SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$ARTIFACT_DIR/execution-receipt-v25.json"
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
  --execution-receipt "$ARTIFACT_DIR/execution-receipt-v25.json" \
  > "$ARTIFACT_DIR/post-audit-v25.json"
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
  --checkpoint "$ARTIFACT_DIR/checkpoint-v25.json" \
  --continuation-readiness "$ARTIFACT_DIR/continuation-readiness-v25.json" \
  --request "$ARTIFACT_DIR/request-v25.json" \
  --signed-verification "$ARTIFACT_DIR/signed-authorization-verification-v25.json" \
  --execution-readiness "$ARTIFACT_DIR/execution-readiness-v25.json" \
  --execution-receipt "$ARTIFACT_DIR/execution-receipt-v25.json" \
  --post-audit "$ARTIFACT_DIR/post-audit-v25.json" \
  > "$ARTIFACT_DIR/evidence-bundle-v25.json"
```

The bundle reruns every native artifact validator, checks cross-artifact
lineage, and requires the production DB/WAL/SHM to still match the final audit.
It authorizes no next action.

## 9. Verify sealed evidence handoff

Before handing the completed v25 evidence forward to any separately reviewed
future workflow, revalidate the sealed bundle/post-audit pair against the
current production database:

```sh
python "$SOURCE_TREE/deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_evidence_handoff.py" \
  --repo "$REPO" \
  --source-tree "$SOURCE_TREE" \
  --evidence-bundle "$ARTIFACT_DIR/evidence-bundle-v25.json" \
  --post-audit "$ARTIFACT_DIR/post-audit-v25.json" \
  > "$ARTIFACT_DIR/evidence-handoff-v25.json"
```

Continue with no future workflow unless the report has
`evidence_handoff_ready=true`. This only means the sealed v25 evidence,
post-audit route, pair lineage, and current DB/WAL/SHM still agree. The report
keeps `future_checkpoint_refresh_authorized=false`, requires a fresh operator
preflight for any future sequence, and authorizes no PAPER tick, live
submission, new capital, or promotion.

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
- the final database no longer matches the post-audit before bundling;
- the sealed v25 bundle/post-audit pair fails handoff validation or the current
  DB/WAL/SHM no longer matches the sealed final state.

Recovery, terminal evaluation, another evidence tick, recurring PAPER
collection, live submission, new capital, and promotion all require separate
reviewed workflows and authorization.
