# Manual market/PAPER preservation review

Use this stage only after the production-local research_store.py and state_reader.rs candidates have been reconstructed and validated off production.

This stage is evidence-only. It does not modify /opt/pio, does not materialize candidate contents into the reviewed source tree, and does not authorize any production mutation.

Reviewed source head for this preservation review:

~~~text
69c2303da5dd291d9cf249e45c9749aeb3e582f5
~~~

## Inputs

The review consumes the two sealed portable-validation reports:

- research-store portable validation for candidate blob 31bd88e3d74490f5d0b617ff4b36383e7e12e18f;
- state-reader portable validation for candidate blob f54a1021cf8f89d285bde957d1f72d81857ec2fa.

Both validations must be validation_ready=true, non-authorizing, and produced from the same reviewed source HEAD.

The review also binds the known production evidence:

- production HEAD ebc0b3c8405da30d88a5ee156f31bf041ffb1ad8;
- reconciliation evidence 5ff37e0f8ee17b1593ac96cc8a678f05b13234422929a207efb581324bc69d13;
- state-reader conflict-hunk evidence 805d5e89ecc9d14157a58e5fc40e1cf3b9632b6636fc61580f157890eb4736a7.

## Run the review

Use an isolated source checkout at the reviewed head above:

~~~bash
PRESERVATION_REVIEW="/var/tmp/pio-manual-paper-preservation-review.json"

python3 "$SRC/deploy/tools/build_manual_market_paper_preservation_review.py" \
  --source-tree "$SRC" \
  --research-store-validation /path/to/research-store-portable-validation.json \
  --state-reader-validation /path/to/state-reader-portable-validation.json \
  > "$PRESERVATION_REVIEW"

python3 -m json.tool "$PRESERVATION_REVIEW"
~~~

The command exits nonzero when either portable validation is not ready or the two validations were not produced from the same reviewed source HEAD.

## What a ready review means

preservation_ready=true means the following two substitutions are ready for a **separate reviewed-source rebase**:

### research_store.py

- current production-local blob: f9deb47c10c88a4e1e364dd12d6c7569c3826a98;
- superseded reviewed target: c9b9de5838d95a86bddffa6166b4d7a62e91cf16;
- preserved candidate target: 31bd88e3d74490f5d0b617ff4b36383e7e12e18f.

### state_reader.rs

- current production-local blob: d1267db6708b91bc8cacabffcd397a380866c79a;
- superseded reviewed target: 30d1435af1329bca07f73d6639539b43503e84e9;
- preserved candidate target: f54a1021cf8f89d285bde957d1f72d81857ec2fa.

The review emits no candidate source contents. Each proposed rebase operation is sealed with its own operation SHA-256 and the complete report is sealed as review_sha256.

## Reviewed-source rebase boundary

A ready preservation review is **not** an instruction to copy either candidate into production.

The next code-review stage must materialize the two already-validated candidate contents into a new reviewed source revision and update only the metadata that still names their superseded deployment lineage. In particular, that reviewed source rebase must account for:

- the Phase-2 prerequisite manifest base/target for research_store.py;
- the dedicated state-reader guard base/target and reference patch;
- the manual runtime prerequisite state-reader target;
- downstream pinned Git blobs that intentionally bind those reviewed artifacts.

The production-local blobs become the new expected deployment bases for these two preserved files. The preserved candidate blobs become their new reviewed targets.

That source rebase is a repository review operation, not a production deployment operation.

## Mandatory reset after the reviewed-source rebase

Any previous readiness, handoff, deployment plan, deployment gate, or mutation review was built against the superseded reviewed target lineage.

After a preservation rebase is merged, discard those old artifacts for deployment purposes and start again from:

1. fresh read-only production readiness against the new reviewed source;
2. fresh sealed handoff;
3. fresh deterministic deployment plan;
4. fresh sealed deployment gate;
5. fresh read-only file-level mutation review.

Do not splice a new preservation review into an old plan or gate.

## Authorization boundary

Even when preservation_ready=true:

- candidate_content_included=false;
- production_file_modified=false;
- source_tree_modified=false;
- requires_reviewed_source_rebase=true;
- requires_manifest_rebase=true;
- requires_new_readiness_cycle=true;
- requires_separate_mutation_authorization=true;
- production_deployment_authorized=false;
- mutation_authorized=false;
- service restart, detector cursor movement, PAPER timer enablement, signing, submission, and live capital remain unauthorized.

## Stop boundary

Stop after the sealed preservation review.

Do not run the old handoff/plan/gate/mutation-review artifacts against the preserved candidates. Do not copy candidate files into /opt/pio. Do not alter the detector/watcher services or retained cursor.

The next safe engineering step is a separately reviewed **source/manifests rebase** built from the two validated candidate contents.
