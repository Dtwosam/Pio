# Manual market/PAPER preserved production review

Use this runbook only after the two production-local preservation candidates have
been reconstructed and validated off production.

This is the preservation-aware read-only path. It does **not** copy candidate
files into production, capture backups, restart services, enable timers, move
the detector cursor, sign transactions, submit transactions, or authorize live
capital.

Reviewed code source for this chain:

~~~text
9f9fbdf9ed1e805d055f307e2b78f74872f57fe8
~~~

The two preserved deployment targets are:

- `python-learner/src/meteora_learner/research_store.py`:
  `31bd88e3d74490f5d0b617ff4b36383e7e12e18f`;
- `rust-executor/src/state_reader.rs`:
  `f54a1021cf8f89d285bde957d1f72d81857ec2fa`.

Their expected production-local bases remain:

- research store: `f9deb47c10c88a4e1e364dd12d6c7569c3826a98`;
- state reader: `d1267db6708b91bc8cacabffcd397a380866c79a`.

## 1. Required private artifacts

Keep the following artifacts private. Do not commit candidate contents or
portable patches to public repository history.

Set paths to the exact artifacts produced by the portable validation workflow:

~~~bash
: "${PAPER_ACCOUNT:?Set PAPER_ACCOUNT to the intended virtual PAPER account id}"

: "${RESEARCH_VALIDATION:?Set RESEARCH_VALIDATION to the ready research-store portable-validation JSON}"
: "${STATE_VALIDATION:?Set STATE_VALIDATION to the ready state-reader portable-validation JSON}"

: "${RESEARCH_PATCH_REPORT:?Set RESEARCH_PATCH_REPORT to the sealed research-store patch-report JSON}"
: "${RESEARCH_PATCH:?Set RESEARCH_PATCH to the exact research-store portable patch}"
: "${STATE_PATCH_REPORT:?Set STATE_PATCH_REPORT to the sealed state-reader patch-report JSON}"
: "${STATE_PATCH:?Set STATE_PATCH to the exact state-reader portable patch}"
~~~

Both portable validations must report `validation_ready=true` and must bind the
same reviewed source HEAD.

## 2. Check out the reviewed code source

Never update the production working tree for this step.

~~~bash
set -euo pipefail

REVIEWED_REF="9f9fbdf9ed1e805d055f307e2b78f74872f57fe8"
SRC="$(mktemp -d /var/tmp/pio-preserved-review-source.XXXXXX)"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"
~~~

## 3. Build the sealed preservation review

~~~bash
PRESERVATION_REVIEW="/var/tmp/pio-manual-paper-preservation-review.json"

python3 "$SRC/deploy/tools/build_manual_market_paper_preservation_review.py" \
  --source-tree "$SRC" \
  --research-store-validation "$RESEARCH_VALIDATION" \
  --state-reader-validation "$STATE_VALIDATION" \
  > "$PRESERVATION_REVIEW"

python3 -m json.tool "$PRESERVATION_REVIEW"
~~~

A ready report requires both portable validations to be ready and to name the
same source HEAD. It embeds no candidate contents and keeps every authorization
flag false.

## 4. Build the private preserved-source bundle

The private bundle materializes the two validated candidates only under
`/var/tmp`. It never modifies the production repository or public Git history.

First verify that the reviewed checkout is the same source HEAD used by the
portable validations:

~~~bash
VALIDATION_HEAD="$(python3 - "$PRESERVATION_REVIEW" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    report = json.load(f)
heads = {entry["validation_source_head"] for entry in report["entries"]}
if len(heads) != 1:
    raise SystemExit("portable validation source heads disagree")
print(next(iter(heads)))
PY
)"

test "$(git -C "$SRC" rev-parse HEAD)" = "$VALIDATION_HEAD"
~~~

Then construct the bundle into a path that does not already exist:

~~~bash
STAMP="$(date +%Y%m%d%H%M%S)"
PRIVATE_BUNDLE_DIR="/var/tmp/pio-manual-paper-preserved-source.$STAMP"
PRIVATE_BUNDLE_REPORT="/var/tmp/pio-manual-paper-preserved-source.$STAMP.json"

test ! -e "$PRIVATE_BUNDLE_DIR"

python3 "$SRC/deploy/tools/build_manual_market_paper_preserved_source_bundle.py" \
  --base-source-tree "$SRC" \
  --preservation-review "$PRESERVATION_REVIEW" \
  --research-store-patch-report "$RESEARCH_PATCH_REPORT" \
  --research-store-patch "$RESEARCH_PATCH" \
  --state-reader-patch-report "$STATE_PATCH_REPORT" \
  --state-reader-patch "$STATE_PATCH" \
  --output-dir "$PRIVATE_BUNDLE_DIR" \
  > "$PRIVATE_BUNDLE_REPORT"

python3 -m json.tool "$PRIVATE_BUNDLE_REPORT"
~~~

Require `bundle_ready=true`. The report is content-free; candidate bytes exist
only in the private bundle.

## 5. Run fresh preservation-aware production readiness

~~~bash
PRESERVED_READINESS="/var/tmp/pio-manual-paper-preserved-readiness.json"

python3 "$SRC/deploy/tools/check_manual_market_paper_preserved_readiness.py" \
  --repo /opt/pio \
  --reviewed-source-tree "$SRC" \
  --private-bundle-report "$PRIVATE_BUNDLE_REPORT" \
  --paper-account "$PAPER_ACCOUNT" \
  > "$PRESERVED_READINESS"

python3 -m json.tool "$PRESERVED_READINESS"
~~~

This checker substitutes only the two preserved targets. Every unaffected file
remains bound to the reviewed public manifests.

A nonzero exit means stop. Do not repair production in place to make readiness
pass.

## 6. Capture a fresh sealed preserved handoff

~~~bash
PRESERVED_HANDOFF="/var/tmp/pio-manual-paper-preserved-handoff.json"

python3 "$SRC/deploy/tools/manual_market_paper_preserved_handoff.py" capture \
  --repo /opt/pio \
  --reviewed-source-tree "$SRC" \
  --private-bundle-report "$PRIVATE_BUNDLE_REPORT" \
  --paper-account "$PAPER_ACCOUNT" \
  > "$PRESERVED_HANDOFF"

python3 -m json.tool "$PRESERVED_HANDOFF"
~~~

The handoff binds the private bundle digest, production HEAD, exact tracked
working-tree diff digest, detector/watcher state, PAPER unit state, target pool
cursor, and the complete preserved-readiness state.

## 7. Build the deterministic preserved deployment plan

~~~bash
PRESERVED_PLAN="/var/tmp/pio-manual-paper-preserved-deployment-plan.json"

python3 "$SRC/deploy/tools/build_manual_market_paper_preserved_deployment_plan.py" \
  --source-tree "$SRC" \
  --handoff "$PRESERVED_HANDOFF" \
  --private-bundle-report "$PRIVATE_BUNDLE_REPORT" \
  > "$PRESERVED_PLAN"

python3 -m json.tool "$PRESERVED_PLAN"
~~~

The only preservation-specific operation is `UPDATE_PRESERVED_FILE`, and only
the two reviewed preservation paths may use it. Standard operations remain
manifest-bound `CREATE_FILE` or `UPDATE_FILE` operations.

The plan is non-mutating and includes no candidate contents.

## 8. Run the fresh preserved deployment gate

~~~bash
PRESERVED_GATE="/var/tmp/pio-manual-paper-preserved-deployment-gate.json"

python3 "$SRC/deploy/tools/check_manual_market_paper_preserved_deployment_gate.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --private-bundle-report "$PRIVATE_BUNDLE_REPORT" \
  --handoff "$PRESERVED_HANDOFF" \
  --plan "$PRESERVED_PLAN" \
  > "$PRESERVED_GATE"

python3 -m json.tool "$PRESERVED_GATE"
~~~

The gate derives the PAPER account and target pool from the sealed handoff. It
does not accept caller overrides for either value. It reruns preservation-aware
readiness and rebuilds the preserved plan before reporting `gate_ready=true`.

## 9. Run the preserved file-level mutation review

~~~bash
PRESERVED_MUTATION_REVIEW="/var/tmp/pio-manual-paper-preserved-mutation-review.json"

python3 "$SRC/deploy/tools/build_manual_market_paper_preserved_mutation_review.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --private-bundle-report "$PRIVATE_BUNDLE_REPORT" \
  --handoff "$PRESERVED_HANDOFF" \
  --plan "$PRESERVED_PLAN" \
  --gate "$PRESERVED_GATE" \
  > "$PRESERVED_MUTATION_REVIEW"

python3 -m json.tool "$PRESERVED_MUTATION_REVIEW"
~~~

For `review_ready=true`, the review requires:

- a fresh preserved gate that exactly matches the saved gate;
- every production target to match its exact expected-current state;
- each `UPDATE_PRESERVED_FILE` target to come only from the sealed private
  bundle;
- each standard create/update target to come only from reviewed public source;
- a complete rollback recipe for every operation.

For updates, rollback is the exact expected-current blob and backup capture is
required before any future mutation. This review deliberately keeps
`backup_material_captured=false`.

## 10. Stop boundary

Stop after the preserved mutation review.

Even when all five preserved production stages are ready:

- `candidate_content_included=false`;
- `backup_material_captured=false`;
- `requires_fresh_execution_recheck=true`;
- `requires_separate_mutation_authorization=true`;
- `production_deployment_authorized=false`;
- `mutation_authorized=false`;
- service restart, detector cursor movement, PAPER timer enablement,
  signing/submission, and live capital remain unauthorized.

Do **not** use the regular
`manual_market_paper_handoff.py`,
`build_manual_market_paper_deployment_plan.py`,
`check_manual_market_paper_deployment_gate.py`, or
`build_manual_market_paper_mutation_review.py` for this preserved deployment
lineage.

Do not copy either candidate into production, capture backups, restart services,
move the cursor, enable the PAPER timer, or construct a mutation executor from
this runbook.
