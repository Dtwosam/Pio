# Phase 8 checkpoint v25 operator archive runbook

This runbook is only for terminal archival of an already-completed checkpoint
v25 operator session. It does not create evidence, sign an authorization,
execute a PAPER tick, refresh a checkpoint, submit transactions, or promote a
model.

The execution/operator workflow remains defined by the existing v25 operator
surface and core continuation manifest. This archival layer starts only after
the v25 evidence bundle and evidence handoff already exist.

The machine-readable archival-surface contract is:

`deploy/manifests/phase8-recursive-reentry-checkpoint-v25-operator-archive-surface.json`

## Inputs

Set the archival paths explicitly:

```sh
SOURCE_TREE=/path/to/reviewed/pio-source
ARTIFACT_DIR=/path/to/private/v25-artifacts
SIGNATURE=/path/to/detached-signature
ALLOWED_SIGNERS=/path/to/allowed_signers
```

All inputs must be retained as regular files. Do not replace archived inputs
with symlinks.

## 0. Verify the reviewed archival surface

```sh
python "$SOURCE_TREE/deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface.py" \
  --source-tree "$SOURCE_TREE" \
  > "$ARTIFACT_DIR/operator-archive-surface-v25.json"
```

Continue only if `archive_surface_verified=true`,
`base_operator_surface_verified=true`, and
`archive_layer_authorizes_no_next_action=true`.

This check validates source integrity only. It does not make historical
authorization current or reusable.

## 1. Seal the completed operator session

Run this only after the final artifact-status report says the eight-stage core
chain is complete and the evidence-handoff report is ready.

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_operator_session_archive.py" \
  --source-tree "$SOURCE_TREE" \
  --surface-integrity "$ARTIFACT_DIR/operator-surface-integrity-v25.json" \
  --preflight "$ARTIFACT_DIR/operator-preflight-v25.json" \
  --artifact-status "$ARTIFACT_DIR/artifact-status-v25.json" \
  --evidence-bundle "$ARTIFACT_DIR/evidence-bundle-v25.json" \
  --evidence-handoff "$ARTIFACT_DIR/evidence-handoff-v25.json" \
  > "$ARTIFACT_DIR/operator-session-archive-v25.json"
```

Continue only if `session_lineage_verified=true`,
`archive_read_only=true`, and `historical_archive_only=true`.

The session archive is historical. It does not perform a fresh preflight,
reverify the detached signature, re-read the production database, or authorize
any next action.

## 2. Archive and reverify the raw signing materials

Retain the exact signed payload JSON, emitted signing-bytes file, detached
signature, and allowed-signers trust root from the completed operator session.

```sh
python "$SOURCE_TREE/deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_signing_material_archive.py" \
  --source-tree "$SOURCE_TREE" \
  --request "$ARTIFACT_DIR/request-v25.json" \
  --payload "$ARTIFACT_DIR/signed-payload-v25.json" \
  --signing-bytes "$ARTIFACT_DIR/signing-bytes-v25" \
  --signature "$SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --signed-verification "$ARTIFACT_DIR/signed-authorization-verification-v25.json" \
  --evidence-bundle "$ARTIFACT_DIR/evidence-bundle-v25.json" \
  --session-archive "$ARTIFACT_DIR/operator-session-archive-v25.json" \
  > "$ARTIFACT_DIR/signing-material-archive-v25.json"
```

Continue only for archival purposes if
`signing_material_archive_ready=true`,
`input_files_stable_during_archive=true`,
`cryptographic_signature_verified=true`, and
`authorization_currently_reusable=false`.

The signature is reverified cryptographically under the reviewed v25 SSH
namespace and trust root, but historical TTL validity is deliberately not
rechecked for reuse. A successful archive does not authorize another tick.

## Archive stop conditions

Stop archival and preserve the original files unchanged when any of these
occur:

- the archival source surface does not match its pinned manifest;
- the completed eight-stage artifact chain is not valid and complete;
- the evidence bundle and evidence handoff do not agree;
- raw request or signed-verification file hashes differ from the sealed bundle
  or session archive;
- saved signing bytes differ from the reviewed canonical payload bytes;
- the detached signature fails cryptographic verification;
- the allowed-signers trust-root digest differs from the historical
  verification/bundle;
- any raw archival input changes while the signing-material archive is being
  built.

A completed archive remains non-authorizing. A future checkpoint refresh,
another PAPER tick, recovery, terminal evaluation, recurring PAPER collection,
live submission, new capital, and promotion all require separate reviewed
workflows and authorization.
