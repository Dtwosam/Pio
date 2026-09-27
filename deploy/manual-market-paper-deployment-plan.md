# Manual market/PAPER deployment plan

This step converts a sealed production preflight handoff into a deterministic,
read-only deployment plan.

The plan is **not deployment authorization** and cannot apply anything. The
planner has no production repository argument and does not copy files, run
`git apply`, control systemd, move detector cursors, enable PAPER timers, sign
transactions, or use real capital.

## Inputs

Use the exact reviewed source tree that produced the readiness/handoff
artifacts and a sealed handoff JSON created by
`manual_market_paper_handoff.py`.

```bash
python3 "$SRC/deploy/tools/build_manual_market_paper_deployment_plan.py" \
  --source-tree "$SRC" \
  --handoff /var/tmp/pio-manual-paper-preflight-handoff.json \
  > /var/tmp/pio-manual-paper-deployment-plan.json
```

The planner independently verifies pinned blobs for:

- the sealed handoff tool;
- the selective Phase-2 shared-store prerequisite manifest;
- both market/PAPER runtime manifests;
- the dedicated state-reader guard;
- the reviewed state-reader patch.

It also verifies that the runtime and prerequisite file sets do not overlap,
that both runtime manifests describe the same runtime files/blobs, and that the
state-reader patch modifies only `rust-executor/src/state_reader.rs`.

## Layer order

Operations are emitted only in this order:

1. `PHASE2_SHARED_PREREQUISITES`
   - `research_store.py`
   - `storage.py`
2. `STATE_READER`
   - the exact reviewed patch for `rust-executor/src/state_reader.rs`
3. `MARKET_PAPER_RUNTIME`
   - runtime-only market research/PAPER files

Files already at their reviewed targets do not appear as operations.

Each create/update operation records its expected current Git blob and reviewed
target blob. Update and patch operations are marked as requiring a backup.
Create operations require the target path to be absent.

## Plan identity

The JSON includes:

- the sealed handoff state SHA-256;
- all reviewed source artifact blobs;
- the ordered operation list;
- `plan_sha256`, a canonical digest of the complete plan identity;
- `requires_fresh_handoff_verification=true`.

Every authorization field remains false, including
`production_deployment_authorized` and `mutation_authorized`.

An empty operation list is valid when every required production file is already
at its reviewed target.

## Fresh read-only gate

After saving the plan, run
`check_manual_market_paper_deployment_gate.py` immediately before any future
separately reviewed mutation. The gate validates this plan, rebuilds it from the
reviewed source and handoff, re-runs current production readiness, and fails if
any bound production state has drifted.

## Stop boundary

Do not turn the plan into file mutations from this step. A passing fresh gate
still reports `requires_separate_mutation_authorization=true` and
`mutation_authorized=false`.

Any later executor must be separately reviewed and must preserve backups and
rollback for every update/patch, leave detector/watcher services running unless
a concrete failure separately requires intervention, and never move the Phase-2
detector cursor as part of PAPER deployment.
