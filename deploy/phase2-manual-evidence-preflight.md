# Phase-2 manual evidence deployment preflight

This runbook is **read-only**. It does not authorize deployment.

The goal is to prove whether the reviewed manual-evidence source stack can be
selectively overlaid onto the dirty production checkout without overwriting
production-local changes.

## Hard boundaries

- Do not run `git pull`, `git checkout`, `git reset`, `git restore`, or
  `git clean` inside `/opt/pio`.
- Do not restart, stop, or reload the detector or watcher.
- Do not manually edit or advance the Phase-2 detector cursor.
- Do not pass `--apply` to the collection-stack deployment tool.
- Do not enable the evidence-cycle timer.
- Stop if either production service is not active or the detector is reporting
  a concrete inspection/batch failure.

## 1. Read-only production health

Record service state and the current detector cursor before any source
preflight. The detector cursor must be observed as-is; never modify it.

Use the existing production health procedure for:

- `pio-phase2-add-detector.service`
- `pio-phase2-prestate-watch.service`
- recent `INSPECT_ERROR` / `BATCH_RETRY` counts
- the current persisted cursor for the target pool

If a detector batch is still in flight, the source preflight may be inspected,
but deployment authorization must remain blocked.

## 2. Prepare a clean source tree outside production

Use a new directory under `/var/tmp`. Never switch the Git state of
`/opt/pio`.

After this PR stack is merged, substitute the reviewed merged commit for
`<REVIEWED_COMMIT>`:

```bash
set -euo pipefail

SRC="/var/tmp/pio-phase2-manual-evidence-$(date -u +%Y%m%dT%H%M%SZ)"
git clone --no-checkout https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" fetch --no-tags origin <REVIEWED_COMMIT>
git -C "$SRC" checkout --detach <REVIEWED_COMMIT>
```

## 3. Run both preflights only

The manual evidence source deployment is intentionally split into two reviewed
guards.

### 3a. Generic source overlay preflight

Run the collection-stack tool from the clean source tree. Its reviewed manifest
is deliberately apply-locked and excludes
`rust-executor/src/state_reader.rs`.

```bash
python3 "$SRC/deploy/tools/apply_phase2_collection_stack.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --manifest "$SRC/deploy/manifests/phase2-collection-integration.json"
```

Do not pass `--apply`.

### 3b. Dedicated state-reader patch preflight

Run the reviewed PR #21-derived guard separately for
`rust-executor/src/state_reader.rs`:

```bash
python3 "$SRC/deploy/tools/apply_phase2_single_slot_stack_patch.py" \
  --repo /opt/pio \
  --patch "$SRC/deploy/patches/phase2-single-slot-stack-state-reader.patch"
```

Do not pass `--apply`. This guard verifies the exact reviewed patch bytes and
runs its patch applicability checks without changing the production file.

## 4. Required preflight result

Before any later deployment-authorization review, all of the following must
hold:

- `content_ready = true`
- `production_deployment_authorized = false`
- `deployment_guard_apply_locked = true`
- `apply_authorized = false`
- `applied = false`
- every file status is one of:
  - `ALREADY_TARGET`
  - `READY_CREATE`
  - `READY_UPDATE`

Any `CONFLICT_*`, `SOURCE_*`, or outside-tree status from the generic
overlay is a hard stop. Any non-applicable or local-conflict result from the
dedicated state-reader guard is also a hard stop.

A conflict is evidence that production contains bytes not represented by the
reviewed base/target lineage. Capture the path plus current/base/target blob
identifiers for review. Do not overwrite the file.

## 5. What this preflight does not prove

A clean source preflight does not authorize deployment and does not prove that a
manual evidence cycle should run immediately. A later authorization review must
also confirm:

- the detector is healthy and any active backlog has reached a safe natural
  commit point;
- production-local conflicts from both deployment guards have been reconciled explicitly;
- the exact source tree and manifest remain the reviewed bytes;
- no service restart or detector cursor movement is required;
- the first evidence cycle will be run manually and bounded before any timer is
  enabled.

Only after those checks should a separate reviewed authorization change unlock
the guarded overlay.
