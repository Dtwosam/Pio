# Preserved manual market/PAPER authorization review

This is the final machine-generated review stage in the preservation-aware
manual market/PAPER deployment chain.

Reviewed authorization-review source:

```text
3b9c8c50a8c4ca18d04bc73796d51d4010cc99f8
```

The tool is read-only. It does **not** grant deployment or mutation permission.

## Inputs

Run it only after all of these already exist and are ready:

- the sealed private preserved-source bundle report;
- the sealed preserved handoff;
- the deterministic preserved deployment plan;
- the saved preserved deployment gate;
- the preserved mutation review;
- the complete preserved evidence package; and
- the sealed rollback-backup capture report and its private backup directory.

The stage re-runs the live preserved mutation review against current production,
requires exact equality with the saved review, and re-reads every rollback
backup with the same no-follow safety rules used during capture.

## Run

Use one isolated reviewed-source checkout:

```bash
set -euo pipefail

REVIEWED_REF="3b9c8c50a8c4ca18d04bc73796d51d4010cc99f8"
SRC="$(mktemp -d /var/tmp/pio-preserved-auth-review-source.XXXXXX)"

PRIVATE_BUNDLE_REPORT="/var/tmp/pio-manual-paper-preserved-source-bundle.json"
HANDOFF="/var/tmp/pio-manual-paper-preserved-handoff.json"
PLAN="/var/tmp/pio-manual-paper-preserved-deployment-plan.json"
GATE="/var/tmp/pio-manual-paper-preserved-deployment-gate.json"
MUTATION_REVIEW="/var/tmp/pio-manual-paper-preserved-mutation-review.json"
EVIDENCE_PACKAGE="/var/tmp/pio-manual-paper-preserved-evidence-package.json"

# Set this to the exact report created by the rollback-backup capture stage.
BACKUP_CAPTURE="/var/tmp/pio-manual-paper-preserved-backups.<STAMP>.json"

AUTH_REVIEW="/var/tmp/pio-manual-paper-preserved-authorization-review.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

python3 "$SRC/deploy/tools/check_manual_market_paper_preserved_authorization_review.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --private-bundle-report "$PRIVATE_BUNDLE_REPORT" \
  --handoff "$HANDOFF" \
  --plan "$PLAN" \
  --gate "$GATE" \
  --mutation-review "$MUTATION_REVIEW" \
  --evidence-package "$EVIDENCE_PACKAGE" \
  --backup-capture "$BACKUP_CAPTURE" \
  > "$AUTH_REVIEW"

python3 -m json.tool "$AUTH_REVIEW"
```

Replace `<STAMP>` with the exact backup-capture report path from the prior
stage. Do not guess or select an unrelated backup bundle.

## Ready result

`authorization_review_ready=true` requires all of the following:

- the evidence package, saved mutation review, and rollback-backup capture all
  validate and remain digest-bound to one another;
- the full preserved mutation review can be rebuilt from current production;
- the fresh mutation review is byte-for-byte identical to the saved review;
- every planned operation remains ready;
- every captured rollback file is re-read with no-follow checks;
- every rollback file still matches its sealed Git blob, SHA-256, size, and
  mode; and
- the backup directory remains private.

The report seals the full scope as
`authorization_review_sha256`.

It includes the exact per-operation:

- path and layer;
- operation type;
- expected-current blob;
- target blob;
- rollback operation/blob;
- backup identity; and
- freshly observed backup identity.

It does not contain candidate source contents.

## Non-authorization boundary

Even when `authorization_review_ready=true`, the report must still contain:

- `authorization_granted=false`;
- `requires_explicit_human_authorization=true`;
- `requires_fresh_execution_recheck=true`;
- `requires_separate_mutation_authorization=true`;
- `production_deployment_authorized=false`;
- `mutation_authorized=false`;
- service restart authorization false;
- detector cursor movement authorization false;
- PAPER timer enablement authorization false; and
- live-capital authorization false.

This stage is therefore a review package, not an authorization token.

## Stop boundary

Stop here.

Do **not** copy files into production, create/delete planned production files,
restart detector or watcher services, edit the detector cursor, enable the
PAPER timer, sign transactions, submit transactions, or deploy capital from
this runbook.

Any future mutation implementation must be separately reviewed and must bind
an explicit human authorization to this exact
`authorization_review_sha256`, then perform another immediate per-operation
expected-current recheck before the first write.

Broad Git operations over the production working tree remain prohibited.
