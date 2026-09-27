# Manual market/PAPER deployment gate

This is the final read-only check immediately before any future separately
reviewed production mutation.

It binds three things together:

1. the saved sealed production handoff;
2. the saved deterministic deployment plan;
3. a fresh read-only production readiness result from the current /opt/pio
   tree.

A passing gate still does **not** authorize deployment.

## Run the gate

Use the same reviewed source lineage used to build the handoff and plan:

~~~bash
python3 "$SRC/deploy/tools/check_manual_market_paper_deployment_gate.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --handoff /var/tmp/pio-manual-paper-preflight-handoff.json \
  --plan /var/tmp/pio-manual-paper-deployment-plan.json
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

Even when the gate passes:

- **requires_separate_mutation_authorization=true**;
- **production_deployment_authorized=false**;
- **mutation_authorized=false**;
- service restart, detector cursor movement, PAPER timer enablement and live
  capital remain unauthorized.

## Stop boundary

Do not interpret a passing gate as permission to mutate /opt/pio.

The next step, if production deployment is explicitly reviewed later, is a
separate selective executor bound to the exact gate inputs and plan digest. It
must not use broad Git operations, must re-check every expected-current blob
immediately before each operation, and must provide rollback for every update
or patch.
