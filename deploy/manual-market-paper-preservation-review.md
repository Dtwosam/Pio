# Manual market/PAPER preservation review

Use this stage only after the production-local `research_store.py` and
`state_reader.rs` candidates have been reconstructed and validated off
production.

This stage is evidence-only. It does not modify the production repository,
publish candidate contents, or authorize any production mutation.

Current preservation-aware code chain:

~~~text
9f9fbdf9ed1e805d055f307e2b78f74872f57fe8
~~~

## Inputs

The review consumes the two sealed portable-validation reports:

- research-store candidate
  `31bd88e3d74490f5d0b617ff4b36383e7e12e18f`;
- state-reader candidate
  `f54a1021cf8f89d285bde957d1f72d81857ec2fa`.

Both validations must have `validation_ready=true`, remain non-authorizing, and
be produced from the same reviewed source HEAD.

The review also binds the known production evidence:

- production HEAD
  `ebc0b3c8405da30d88a5ee156f31bf041ffb1ad8`;
- reconciliation evidence
  `5ff37e0f8ee17b1593ac96cc8a678f05b13234422929a207efb581324bc69d13`;
- state-reader conflict-hunk evidence
  `805d5e89ecc9d14157a58e5fc40e1cf3b9632b6636fc61580f157890eb4736a7`.

## Run the preservation review

~~~bash
PRESERVATION_REVIEW="/var/tmp/pio-manual-paper-preservation-review.json"

python3 "$SRC/deploy/tools/build_manual_market_paper_preservation_review.py" \
  --source-tree "$SRC" \
  --research-store-validation "$RESEARCH_VALIDATION" \
  --state-reader-validation "$STATE_VALIDATION" \
  > "$PRESERVATION_REVIEW"

python3 -m json.tool "$PRESERVATION_REVIEW"
~~~

The command exits nonzero when either portable validation is not ready or the
two validations do not share one reviewed source HEAD.

## What a ready preservation review means

`preservation_ready=true` seals exactly two candidate substitutions:

### research_store.py

- production-local base:
  `f9deb47c10c88a4e1e364dd12d6c7569c3826a98`;
- superseded public reviewed target:
  `c9b9de5838d95a86bddffa6166b4d7a62e91cf16`;
- preserved candidate:
  `31bd88e3d74490f5d0b617ff4b36383e7e12e18f`.

### state_reader.rs

- production-local base:
  `d1267db6708b91bc8cacabffcd397a380866c79a`;
- superseded public reviewed target:
  `30d1435af1329bca07f73d6639539b43503e84e9`;
- preserved candidate:
  `f54a1021cf8f89d285bde957d1f72d81857ec2fa`.

The report embeds no candidate source contents. A ready review does **not** mean
that either candidate should be committed to public repository history or
copied into production.

## Private-bundle continuation

The reviewed continuation is now a private preserved-source bundle, not a
public source/manifests rebase.

`deploy/tools/build_manual_market_paper_preserved_source_bundle.py`:

- requires the exact common source HEAD used by the portable validations;
- validates the preservation review plus both sealed portable patch reports and
  patch bytes;
- materializes the two preserved candidates only under a new directory in
  `/var/tmp`;
- verifies the resulting Git blobs, SHA-256 values, and sizes;
- emits a content-free sealed bundle report;
- never reads or writes the production repository.

After a ready private bundle, continue only through the preservation-aware
read-only chain documented in
`manual-market-paper-preserved-production-review.md`:

1. preserved production readiness;
2. sealed preserved handoff;
3. deterministic preserved deployment plan;
4. fresh preserved deployment gate;
5. preserved file-level mutation review;
6. stop for separate authorization/review.

The old regular handoff/plan/gate/mutation-review artifacts are not compatible
with this preserved candidate lineage.

## Authorization boundary

Even when `preservation_ready=true`:

- candidate contents remain private;
- `production_file_modified=false`;
- `source_tree_modified=false`;
- `requires_separate_mutation_authorization=true`;
- `production_deployment_authorized=false`;
- `mutation_authorized=false`;
- service restart, detector cursor movement, PAPER timer enablement,
  signing/submission, and live capital remain unauthorized.

## Stop boundary

Do not copy candidate files into the production repository, publish the private
patches/candidate contents, alter detector/watcher services, or move the
retained cursor.

The next safe step is the private-bundle and preservation-aware read-only chain
in `manual-market-paper-preserved-production-review.md`.
