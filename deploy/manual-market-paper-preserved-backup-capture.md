# Preserved manual market/PAPER rollback backup capture

This stage runs **after** the complete preservation-aware read-only evidence chain
has produced:

- a ready preserved mutation review; and
- a complete preserved evidence package that binds readiness -> handoff -> plan
  -> gate -> mutation review.

Reviewed backup-capture source:

```text
2099e871c610c597af460df6d971dac8f32d8f0d
```

The stage is production-read-only. It copies rollback bytes into a private
directory under `/var/tmp`; it does not change production files, Git state,
services, detector cursor state, the PAPER timer, signing state, or capital.

## Why this stage exists

The preserved mutation review deliberately seals rollback recipes while keeping
`backup_material_captured=false`. Any update operation must have its exact
expected-current bytes available before a later separately authorized mutation
could be considered.

`deploy/tools/capture_manual_market_paper_preserved_backups.py` closes only
that rollback-material gap.

For every planned operation it:

- validates the complete preserved evidence package and the exact preserved
  mutation review;
- requires their plan, mutation-review, and private-bundle digests to agree;
- re-reads the production target immediately before capture;
- rejects symlink targets and symlinked/missing/non-directory parents;
- opens update targets read-only with no-follow semantics;
- requires the live Git blob to equal the exact expected-current/rollback blob;
- copies update bytes only into a newly created private `/var/tmp` backup tree;
- writes backup files with private permissions;
- verifies each copied Git blob, SHA-256, and size;
- re-reads the production target after capture and requires it still to equal
  the expected-current blob;
- records no backup bytes for `CREATE_FILE` operations because their reviewed
  rollback is deletion of the newly created file.

The report contains backup metadata and hashes, not the backup file contents.

## Run

Use the same isolated reviewed source revision for the backup tool:

```bash
set -euo pipefail

REVIEWED_REF="2099e871c610c597af460df6d971dac8f32d8f0d"
SRC="$(mktemp -d /var/tmp/pio-preserved-backup-source.XXXXXX)"
STAMP="$(date +%Y%m%d%H%M%S)"

EVIDENCE_PACKAGE="/var/tmp/pio-manual-paper-preserved-evidence-package.json"
MUTATION_REVIEW="/var/tmp/pio-manual-paper-preserved-mutation-review.json"
BACKUP_DIR="/var/tmp/pio-manual-paper-preserved-backups.$STAMP"
BACKUP_REPORT="/var/tmp/pio-manual-paper-preserved-backups.$STAMP.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

python3 "$SRC/deploy/tools/capture_manual_market_paper_preserved_backups.py" \
  --source-tree "$SRC" \
  --evidence-package "$EVIDENCE_PACKAGE" \
  --mutation-review "$MUTATION_REVIEW" \
  --output-dir "$BACKUP_DIR" \
  > "$BACKUP_REPORT"

python3 -m json.tool "$BACKUP_REPORT"
```

Do not reuse a backup output directory. The tool requires a new path under
`/var/tmp` and removes its partial output if capture fails.

## Ready result

`backup_capture_ready=true` requires all of the following:

- every production target still matches the expected-current state;
- every update target was captured;
- every captured backup blob equals the exact rollback blob;
- every production update target still matches after its backup was copied;
- every create target remains absent as required.

The report is sealed as `backup_capture_sha256`.

Even when ready, it also requires:

- `requires_fresh_execution_recheck=true`;
- `requires_separate_mutation_authorization=true`;
- `production_deployment_authorized=false`;
- `mutation_authorized=false`;
- service restart, detector cursor movement, PAPER timer enablement, signing,
  submission, and live capital remain unauthorized.

## Stop boundary

A ready backup capture is rollback evidence, not permission to change
production.

Do **not** copy reviewed targets into production, delete/create planned files,
restart detector/watcher services, edit the detector cursor, enable the PAPER
timer, sign transactions, or deploy capital from this runbook.

Any future mutation implementation must separately bind at least:

1. the complete preserved evidence-package digest;
2. the preserved mutation-review digest;
3. the backup-capture digest and every backup blob;
4. a fresh execution-time expected-current recheck for every operation; and
5. an explicit separately reviewed mutation authorization.

Broad Git operations over the production working tree remain prohibited.
