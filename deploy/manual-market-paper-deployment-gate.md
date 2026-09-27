# Manual market/PAPER deployment gate

This is the final read-only check immediately before any future separately
reviewed production mutation. It follows the production preflight, sealed
handoff, and deterministic deployment-plan stages; it does not replace them.

It binds three things together:

1. the saved sealed production handoff;
2. the saved deterministic deployment plan;
3. a fresh read-only production readiness result from the current /opt/pio
   tree.

A passing gate still does **not** authorize deployment. The emitted report is
itself sealed with a canonical `gate_sha256` so the exact gate evidence can be
reviewed later without treating mutable console text as authority.

## Run the gate

Use the same reviewed source lineage used to build the handoff and plan:

~~~bash
GATE_REPORT=/var/tmp/pio-manual-paper-deployment-gate.json

python3 "$SRC/deploy/tools/check_manual_market_paper_deployment_gate.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --handoff /var/tmp/pio-manual-paper-preflight-handoff.json \
  --plan /var/tmp/pio-manual-paper-deployment-plan.json \
  > "$GATE_REPORT"

cat "$GATE_REPORT"
~~~

The gate derives the PAPER account and target pool from the sealed handoff. They
are not accepted as replacement command-line values.

## What it checks

Before reading current production, the gate:

- validates the saved handoff and its state digest;
- validates the saved deployment plan and its plan digest;
- verifies the reviewed planner/handoff source blobs;
- rebuilds the deployment plan from the saved handoff and reviewed source;
- requires the rebuilt plan to exactly match the saved plan.

It then re-runs the existing read-only production readiness path. That path
reads:

- production Git HEAD and the exact tracked working-tree diff hash;
- detector/watcher active state;
- the selected PAPER service/timer state;
- the retained target-pool detector cursor;
- selective shared-store prerequisite status;
- dedicated state-reader status;
- runtime-only market/PAPER overlay status.

No source file is copied or patched during this gate.

## Result

**gate_ready=true** requires all of the following:

- the saved handoff was ready;
- the fresh current handoff is ready;
- the fresh production-state identity exactly matches the saved handoff;
- the saved plan points at that handoff state;
- rebuilding the plan from reviewed source produces the exact saved plan.

**changed_sections** identifies top-level handoff fields that drifted.

**gate_sha256** seals the complete reviewed gate identity: the handoff/plan
digests, fresh-state match result, changed-section list, operation/deployment
flags, readiness evidence, and all authorization locks. The tool validates that
schema and digest before emitting the report. Re-hashing a modified report does
not make an authorization flip or contradictory readiness evidence valid.

Even when the gate passes:

- **requires_separate_mutation_authorization=true**;
- **production_deployment_authorized=false**;
- **mutation_authorized=false**;
- service restart, detector cursor movement, PAPER timer enablement and live
  capital remain unauthorized.

## Stop boundary

Do not interpret a passing gate as permission to mutate /opt/pio.

The next reviewed step is still read-only: run
`manual-market-paper-mutation-review.md`. That stage re-runs this gate, requires
the fresh gate to exactly match this saved sealed report, re-checks every
expected-current production file blob or required absence, verifies source and
patch bytes, and emits rollback requirements.

Only after a passing sealed mutation review could a separate selective executor
be considered for its own review. The gate remains point-in-time evidence, and
any future executor must still perform an immediate fresh expected-current
recheck before each operation. Broad Git operations over `/opt/pio` remain
prohibited.
