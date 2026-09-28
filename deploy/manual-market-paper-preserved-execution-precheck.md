# Preserved manual market/PAPER execution precheck

This is the final read-only gate before any separately reviewed production writer.

Reviewed execution-precheck source:

```text
16ac4f8e3546e88ce05d21984ac5f45dfd4de341
```

Reviewed execution-precheck tool blob:

```text
0e30043609b960e1fea9a2289bff777ce1d8290d
```

The precheck may inspect current production state, rollback backups, and signed
authorization evidence. It does **not** write production files or make the
mutation executable.

## Required inputs

Run only after all of these exist and validate:

- the sealed private preserved-source bundle report;
- the preserved handoff, plan, gate, mutation review, and evidence package;
- the private rollback-backup capture;
- the saved terminal authorization review;
- the non-authorizing human-authorization request;
- the saved signed-human-authorization verification;
- the canonical signed payload and detached SSH signature;
- the externally provisioned `allowed_signers` file; and
- the independently pinned SHA-256 of that trust file.

The short-lived human authorization must still be within its validity window.

## Run

Use one isolated reviewed-source checkout:

```bash
set -euo pipefail

REVIEWED_REF="16ac4f8e3546e88ce05d21984ac5f45dfd4de341"
SRC="$(mktemp -d /var/tmp/pio-preserved-execution-precheck-source.XXXXXX)"

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

ALLOWED_SIGNERS="/secure/trust/pio-preserved-allowed-signers"
EXPECTED_ALLOWED_SIGNERS_SHA256="<OUT_OF_BAND_PINNED_SHA256>"

EXECUTION_PRECHECK="/var/tmp/pio-manual-paper-preserved-execution-precheck.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

python3 "$SRC/deploy/tools/check_manual_market_paper_preserved_execution_precheck.py" \
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
  > "$EXECUTION_PRECHECK"

python3 -m json.tool "$EXECUTION_PRECHECK"
```

Replace `<STAMP>` with the exact rollback-backup capture already bound into
the saved authorization review. Do not select a different backup tree.

## What is rechecked

A ready precheck rebuilds the terminal authorization review against current
production and requires byte-for-byte equality with the saved review.

It also:

- re-verifies the detached human signature against the externally pinned trust
  root and current expiry;
- requires the fresh signed verification to equal the saved verification;
- requires the authorization request to bind the exact terminal review;
- requires operation scope equality across request and terminal review;
- rebinds each expected-current blob, target blob, rollback action, and backup
  identity;
- requires rollback-backup integrity to remain ready; and
- requires every operation to remain prechecked.

The sealed output is `execution_precheck_sha256`.

## Ready result

A ready report may contain:

- `preserved_file_mutation_authorization_present=true`;
- `human_authorization_verified=true`;
- `approval_not_expired=true`;
- `backup_integrity_ready=true`;
- `all_operations_prechecked=true`; and
- `execution_precheck_ready=true`.

Those values mean the exact approved preserved-file mutation scope is still
current at precheck time.

They do **not** make any write executable.

The report must still contain:

- `requires_immediate_per_operation_recheck=true`;
- `execution_ready=false`;
- `production_file_modified=false`;
- `production_repository_git_mutated=false`;
- `production_deployment_authorized=false`;
- `mutation_authorized=false`;
- service restart authorization false;
- detector cursor movement authorization false;
- PAPER timer enablement authorization false; and
- live-capital authorization false.

## Stop boundary

Stop here.

A passing execution precheck is the last read-only evidence artifact. It is not
a writer and is not a blanket deployment permit.

Any future writer must be separately reviewed and must:

- consume this exact `execution_precheck_sha256`;
- restrict itself to the exact preserved-file operation scope already sealed;
- immediately re-read the one operation's expected-current state immediately
  before that operation's write;
- fail closed before the write if the expected state differs;
- use only the already reviewed target bytes for that operation;
- preserve the sealed rollback material;
- avoid broad Git operations;
- perform no service restart, detector cursor movement, PAPER timer enablement,
  transaction signing/submission, or live-capital action; and
- stop after the approved file-mutation scope rather than chaining into
  activation.

No production bytes are changed by this runbook.
