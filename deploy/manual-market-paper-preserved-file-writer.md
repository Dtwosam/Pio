# Preserved manual market/PAPER file mutation writer

This stage applies only the exact production file-mutation scope already sealed
by the final preserved execution precheck.

Reviewed writer source:

```text
48f20708e6ddf959dea019103bc098f9b3ce9452
```

Reviewed writer tool blob:

```text
04ab89cb673609daec60336b52bfdfe3f57239be
```

This is a file writer, not an activation tool. It does not run Git mutation
commands, restart services, move detector state, enable the PAPER timer, sign
or submit transactions, or deploy capital.

## Required boundary

Do not run the writer unless the immediately preceding execution precheck is
ready and the exact `execution_precheck_sha256` is independently selected for
this invocation.

The writer itself re-runs the complete execution precheck under an exclusive
writer lock and requires the fresh report to equal the saved report exactly.

That fresh replay re-verifies:

- current production expected state;
- the terminal authorization review;
- the short-lived human detached signature and expiry;
- the independently pinned `allowed_signers` digest;
- rollback-backup integrity; and
- the exact operation/target/rollback scope.

## Run

Use the reviewed source checkout used for the approved chain:

```bash
set -euo pipefail

REVIEWED_REF="48f20708e6ddf959dea019103bc098f9b3ce9452"
SRC="$(mktemp -d /var/tmp/pio-preserved-file-writer-source.XXXXXX)"

PRIVATE_BUNDLE_REPORT="/var/tmp/pio-manual-paper-preserved-source-bundle.json"
HANDOFF="/var/tmp/pio-manual-paper-preserved-handoff.json"
PLAN="/var/tmp/pio-manual-paper-preserved-deployment-plan.json"
GATE="/var/tmp/pio-manual-paper-preserved-deployment-gate.json"
MUTATION_REVIEW="/var/tmp/pio-manual-paper-preserved-mutation-review.json"
EVIDENCE_PACKAGE="/var/tmp/pio-manual-paper-preserved-evidence-package.json"
BACKUP_CAPTURE="/var/tmp/pio-manual-paper-preserved-backups.<STAMP>.json"
AUTH_REVIEW="/var/tmp/pio-manual-paper-preserved-authorization-review.json"
AUTH_REQUEST="/var/tmp/pio-manual-paper-preserved-human-authorization-request.json"
AUTH_VERIFICATION="/var/tmp/pio-manual-paper-preserved-signed-human-authorization-verification.json"
AUTH_PAYLOAD="/var/tmp/pio-manual-paper-preserved-signed-human-authorization-payload.json"
SIGNATURE="/var/tmp/pio-manual-paper-preserved-signed-human-authorization.bin.sig"
EXECUTION_PRECHECK="/var/tmp/pio-manual-paper-preserved-execution-precheck.json"

ALLOWED_SIGNERS="/secure/trust/pio-preserved-allowed-signers"
EXPECTED_ALLOWED_SIGNERS_SHA256="<OUT_OF_BAND_PINNED_SHA256>"
EXPECTED_EXECUTION_PRECHECK_SHA256="<EXACT_REVIEWED_EXECUTION_PRECHECK_SHA256>"

MUTATION_RECEIPT="/var/tmp/pio-manual-paper-preserved-file-mutation-receipt.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

python3 "$SRC/deploy/tools/apply_manual_market_paper_preserved_file_mutations.py" \
  --apply-exact-preserved-file-mutations \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --private-bundle-report "$PRIVATE_BUNDLE_REPORT" \
  --handoff "$HANDOFF" \
  --plan "$PLAN" \
  --gate "$GATE" \
  --mutation-review "$MUTATION_REVIEW" \
  --evidence-package "$EVIDENCE_PACKAGE" \
  --backup-capture "$BACKUP_CAPTURE" \
  --authorization-review "$AUTH_REVIEW" \
  --authorization-request "$AUTH_REQUEST" \
  --signed-authorization-verification "$AUTH_VERIFICATION" \
  --signed-payload "$AUTH_PAYLOAD" \
  --signature "$SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  --execution-precheck "$EXECUTION_PRECHECK" \
  --expected-execution-precheck-sha256 "$EXPECTED_EXECUTION_PRECHECK_SHA256" \
  > "$MUTATION_RECEIPT"

python3 -m json.tool "$MUTATION_RECEIPT"
```

Replace `<STAMP>` only with the rollback capture already bound into the saved
authorization review.

The expected execution-precheck digest is not inferred by the writer. It must
match the exact reviewed precheck selected for this mutation.

## Mutation mechanics

Before changing the first target, the writer:

- acquires an exclusive private writer lock;
- re-runs and matches the complete execution precheck;
- resolves preserved target bytes only from the sealed private source bundle;
- resolves standard target bytes only from reviewed source;
- verifies every target Git blob;
- loads and re-verifies all required rollback bytes into memory; and
- requires the human authorization to remain unexpired.

For every operation, the writer:

1. checks the expected-current production state;
2. stages the already verified target bytes;
3. immediately re-checks the expected-current state again directly before
   changing the target path;
4. installs only that reviewed target;
5. fsyncs the containing directory; and
6. re-reads the target and verifies its exact target blob and mode.

Updates preserve the sealed pre-mutation mode. New files are accepted only when
their reviewed source mode is exactly `0644`.

The writer never creates missing production parent directories.

## Failure rollback

If a later operation fails, every earlier successful operation is rolled back
in reverse order.

An update is restored from the already verified private rollback bytes. A file
created by this writer is deleted only while it still matches the exact target
blob the writer installed.

If the current operation changed its target but failed before verified success,
that operation first attempts to restore itself before the outer reverse
rollback begins.

Any incomplete rollback is reported as a hard failure. The writer does not
overwrite an unexpected drifted file merely to force rollback.

## Successful receipt

A complete run emits a sealed `receipt_sha256` with:

- the exact `execution_precheck_sha256`;
- approver principal, approval ID and expiry;
- one result for every sealed operation;
- expected-current, before, target and after blobs;
- target mode;
- confirmation of the immediate expected-state recheck;
- target-source and rollback-material reverification; and
- post-write verification.

A successful receipt requires:

- `all_operations_applied=true`;
- `file_mutation_completed=true`;
- `requires_post_mutation_validation=true`;
- `rollback_performed=false`;
- `production_repository_git_mutated=false`;
- `service_restart_performed=false`;
- `detector_cursor_moved=false`;
- `paper_timer_enabled=false`;
- `transaction_signed=false`;
- `transaction_submitted=false`; and
- `live_capital_deployed=false`.

## Stop boundary

Stop here after the file-mutation receipt.

Do not restart services, edit the detector cursor, enable the PAPER timer, sign
or submit transactions, or deploy capital from this runbook.

The next safe stage is a new read-only post-mutation audit. It must validate
this exact receipt, re-read every mutated target, verify rollback material
remains available, and confirm that the file mutation did not silently become
an activation step.

File mutation success alone is not PAPER activation and is not live-capital
authorization.
