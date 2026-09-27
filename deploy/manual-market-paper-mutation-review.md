# Manual market/PAPER mutation review

This is the final read-only file-level review after a passing sealed deployment
gate. It does **not** deploy files or authorize deployment.

Reviewed source head for this review:

```text
531d17d7e07111c0ed143c41bee6317448ca0c33
```

The review consumes the same sealed handoff and deterministic plan plus the
saved sealed deployment-gate report. It then re-runs the fresh deployment gate
and checks every planned file operation against the current production tree.

This stage is valid only for the exact reviewed-source lineage that produced
those handoff/plan/gate artifacts. If blocked readiness was resolved through
preserved production-local candidates, first complete
`manual-market-paper-preservation-review.md`, perform the separately reviewed
source/manifests rebase, and then rebuild **all** readiness/handoff/plan/gate
artifacts from the rebased source. Do not feed preserved candidate evidence into
an old plan or gate.

## Run the review

Use the same isolated reviewed source tree and artifacts from the preceding
read-only stages:

~~~bash
MUTATION_REVIEW=/var/tmp/pio-manual-paper-mutation-review.json

python3 "$SRC/deploy/tools/build_manual_market_paper_mutation_review.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --handoff /var/tmp/pio-manual-paper-preflight-handoff.json \
  --plan /var/tmp/pio-manual-paper-deployment-plan.json \
  --gate /var/tmp/pio-manual-paper-deployment-gate.json \
  > "$MUTATION_REVIEW"

cat "$MUTATION_REVIEW"
~~~

A nonzero exit means the review is not ready. Do not work around that result by
editing production files, changing the detector cursor, restarting services, or
rebuilding the artifacts against an unreviewed source tree.

## What it checks

Before emitting a review, the tool:

- validates the sealed handoff, plan, and saved gate;
- rebuilds the deployment plan from the sealed handoff and reviewed source and
  requires an exact match;
- re-runs the full fresh read-only deployment gate and requires the fresh gate
  to exactly match the saved sealed gate for `review_ready=true`;
- verifies each planned production target is either absent exactly when a
  `CREATE_FILE` expects absence or has the exact expected-current Git blob for
  an update/patch;
- verifies each reviewed source target has the plan's exact target Git blob;
- verifies the dedicated state-reader patch has the exact plan SHA-256;
- rejects absolute, non-normalized, traversal, backslash, symlink-target, and
  symlink-parent hazards;
- emits a rollback recipe for every operation.

The tool only reads files and existing readiness state. It never copies,
patches, deletes, renames, or writes a production file.

## Rollback evidence

For `CREATE_FILE`, the rollback recipe is deletion of the newly created file,
but this review performs no creation.

For `UPDATE_FILE` and `APPLY_REVIEWED_PATCH`, the rollback recipe is restore
of the exact expected-current blob. The review marks those operations as
requiring backup capture before any future mutation, but deliberately keeps:

- **backup_material_captured=false**
- **requires_backup_capture_before_mutation=true** when an update/patch exists.

A future mutation executor must capture and verify the actual rollback bytes
before changing an update/patch target. This review does not create that backup.

## Result

**review_ready=true** requires:

- the fresh deployment gate is still ready;
- the fresh gate exactly matches the saved sealed gate;
- every current production target matches its expected-current state;
- every reviewed source target matches the planned target blob;
- any reviewed patch matches its planned SHA-256;
- every operation has a complete rollback recipe.

The report is sealed as **review_sha256**.

Even when the review passes:

- **requires_fresh_execution_recheck=true**;
- **requires_separate_mutation_authorization=true**;
- **production_deployment_authorized=false**;
- **mutation_authorized=false**;
- service restart, detector cursor movement, PAPER timer enablement, signing,
  submission, and live capital remain unauthorized.

## Stop boundary

A passing mutation review is evidence for a separate production-change review;
it is not a deployment command.

A preservation review is not interchangeable with this mutation review. The
preservation review authorizes neither source rebase nor production mutation;
it only seals the exact candidate substitutions that a later reviewed-source
revision must adopt before a fresh mutation review can exist.

Any future selective mutation implementation must be separately reviewed and
must bind the exact handoff state digest, plan digest, gate digest, and mutation
review digest. It must re-check each expected-current target immediately before
changing it, capture verified backup bytes before every update/patch, verify the
target after each operation, and fail closed with rollback on mismatch. Broad
Git operations over `/opt/pio` remain prohibited.
